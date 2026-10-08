import asyncio
import hmac
import json
import logging
import time
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from app.core.config import settings
from app.core import store
from app.models.schema import IngestRequest, ChatRequest
from app.ingestion.repository import clone, parse_repo
from app.retrieval.engine import Index
from app.retrieval.answer import plan, prompt, validate_citations, verify, suggestions
from app.llm.providers import ProviderPool
from app.api.streaming import keepalive

logging.basicConfig(level=logging.INFO)
pool = ProviderPool()
indexes: dict[str, Index] = {}
jobs: dict[str, list[dict]] = {}
tasks: set[asyncio.Task] = set()
ingestion_lock = asyncio.Lock()
index_lock = asyncio.Lock()
chat_slots = asyncio.Semaphore(2)
rates: dict[str, deque] = defaultdict(deque)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    await pool.discover()
    yield
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    for index in indexes.values():
        index.client.close()


app = FastAPI(title="CodeLens", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins.split(","),
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization"],
)


async def authorize(request: Request) -> None:
    if settings.access_token and not hmac.compare_digest(
        request.headers.get("authorization", ""), f"Bearer {settings.access_token}"
    ):
        raise HTTPException(401, "Access token required")
    host = request.client.host if request.client else "local"
    now = time.monotonic()
    queue = rates[host]
    while queue and queue[0] < now - 60:
        queue.popleft()
    if len(queue) >= 90:
        raise HTTPException(429, "Please wait a minute before trying again")
    queue.append(now)
    if len(rates) > 10000:
        for key in list(rates):
            if not rates[key] or rates[key][-1] < now - 60:
                del rates[key]


def directory(repo: str) -> Path:
    try:
        parsed = uuid.UUID(repo)
    except ValueError:
        raise HTTPException(404, "Repository not found") from None
    path = settings.data_dir / str(parsed)
    if not path.exists():
        raise HTTPException(404, "Repository not found")
    return path


def metadata(repo: str) -> dict:
    path = directory(repo) / "metadata.json"
    if not path.exists():
        raise HTTPException(409, "Repository has not finished indexing")
    return json.loads(path.read_text(encoding="utf-8"))


async def get_index(repo: str) -> Index:
    meta = metadata(repo)
    if meta.get("embedding_model") != settings.embedding_model:
        raise HTTPException(409, "Embedding model changed. Reindex this repository with the current model.")
    async with index_lock:
        if repo not in indexes:
            indexes[repo] = await asyncio.to_thread(Index, directory(repo))
    return indexes[repo]


def sse(event: dict) -> str:
    return "data: " + json.dumps(event) + "\n\n"


async def ingest(repo: str, url: str) -> None:
    loop = asyncio.get_running_loop()

    def progress(stage: str, **data) -> None:
        event = {"type": "progress", "stage": stage, **data}
        loop.call_soon_threadsafe(jobs[repo].append, event)

    async with ingestion_lock:
        path = directory(repo)
        try:
            progress("Cloning", message="Fetching a shallow snapshot from GitHub")
            commit = await asyncio.to_thread(clone, url, path / "repo")
            progress("Parsing", message="Filtering files and resolving syntax trees")
            chunks, meta = await asyncio.to_thread(parse_repo, path / "repo", progress)
            progress(
                "Chunking",
                files=len(meta["files"]),
                chunks=len(chunks),
                message="Symbols and source line ranges extracted",
            )
            index = await asyncio.to_thread(Index, path, chunks, progress)
            indexes[repo] = index
            progress("Indexing", message="Saving vector, keyword, and dependency indexes")
            meta.update(
                id=repo,
                url=url,
                commit=commit,
                name=url.removeprefix("https://github.com/"),
                chunks=len(chunks),
                created=time.time(),
                embedding_model=settings.embedding_model,
            )
            (path / "metadata.json").write_text(json.dumps(meta), encoding="utf-8")
            jobs[repo].append({"type": "done", "repo": meta})
        except Exception as exc:
            logging.exception("Ingestion failed")
            jobs[repo].append({"type": "error", "message": str(exc)[:400]})


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "providers": pool.status, "embedding_configured": bool(settings.embedding_model)}


@app.get("/api/repos", dependencies=[Depends(authorize)])
async def repos() -> list[dict]:
    results = []
    for path in settings.data_dir.glob("*/metadata.json"):
        meta = json.loads(path.read_text(encoding="utf-8"))
        results.append({k: meta[k] for k in ("id", "name", "url", "created", "chunks")})
    return sorted(results, key=lambda r: r["created"], reverse=True)


@app.post("/api/repos", dependencies=[Depends(authorize)])
async def create_repo(body: IngestRequest) -> dict:
    if not settings.embedding_model:
        raise HTTPException(503, "Configure EMBEDDING_MODEL in backend/.env first")
    if (
        len([p for p in settings.data_dir.iterdir() if p.is_dir() and len(p.name) == 36])
        >= settings.max_repos
    ):
        raise HTTPException(
            429, "Repository limit reached. Remove unused repository directories while the server is stopped."
        )
    if sum(not events or events[-1]["type"] not in {"done", "error"} for events in jobs.values()) >= 2:
        raise HTTPException(429, "Two repositories are already queued; try again shortly")
    repo = str(uuid.uuid4())
    (settings.data_dir / repo).mkdir(parents=True)
    jobs[repo] = [{"type": "progress", "stage": "Cloning", "message": "Queued for indexing"}]
    task = asyncio.create_task(ingest(repo, body.url))
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    return {"id": repo}


@app.get("/api/repos/{repo}/events", dependencies=[Depends(authorize)])
async def events(repo: str) -> StreamingResponse:
    directory(repo)

    async def generate():
        cursor = 0
        if repo not in jobs:
            yield sse({"type": "done", "repo": metadata(repo)})
            return
        while True:
            for event in jobs[repo][cursor:]:
                cursor += 1
                yield sse(event)
                if event["type"] in {"done", "error"}:
                    return
            await asyncio.sleep(0.25)

    return StreamingResponse(
        keepalive(generate()),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/repos/{repo}", dependencies=[Depends(authorize)])
async def repo_info(repo: str) -> dict:
    return metadata(repo)


@app.get("/api/repos/{repo}/file", dependencies=[Depends(authorize)])
async def file(repo: str, path: str) -> dict:
    metadata(repo)
    files = await asyncio.to_thread(
        lambda: json.loads((directory(repo) / "files.json").read_text(encoding="utf-8"))
    )
    if path not in files:
        raise HTTPException(404, "File not in indexed snapshot")
    return {"path": path, "text": files[path]}


@app.get("/api/repos/{repo}/conversations/{conversation}", dependencies=[Depends(authorize)])
async def conversation(repo: str, conversation: str) -> dict:
    metadata(repo)
    return {"messages": store.history(conversation, repo)}


@app.post("/api/repos/{repo}/chat", dependencies=[Depends(authorize)])
async def chat(repo: str, body: ChatRequest) -> StreamingResponse:
    meta = metadata(repo)
    conversation_id = body.conversation or str(uuid.uuid4())

    async def generate():
        async with chat_slots:
            try:
                history = store.history(conversation_id, repo)
                yield sse({"type": "status", "message": "Planning retrieval"})
                query_plan = await plan(pool, body.question, history)
                index = await get_index(repo)
                found = await asyncio.to_thread(index.search, query_plan["queries"])
                yield sse({"type": "retrieval", **query_plan, **found})
                answer, warnings, citations = "", [], []
                if not found["confident"]:
                    answer = "I couldn't find that in this repo. Try a specific function, filename, or behavior described in the README."
                    yield sse({"type": "token", "text": answer})
                else:
                    async for event in pool.stream(prompt(body.question, meta, found["chunks"], history)):
                        if event["type"] == "token":
                            answer += event["text"]
                        elif event["type"] == "reset":
                            answer = ""
                        yield sse(event)
                    yield sse({"type": "status", "message": "Checking source ranges and claim support"})
                    answer, citations, warnings = validate_citations(answer, found["chunks"])
                    warnings += await verify(pool, answer, found["chunks"])
                followups = await suggestions(pool, body.question, found["chunks"])
                store.save(
                    conversation_id,
                    repo,
                    [
                        *history,
                        {"role": "user", "content": body.question},
                        {
                            "role": "assistant",
                            "content": answer,
                            "citations": citations,
                            "warnings": warnings,
                        },
                    ],
                )
                yield sse(
                    {
                        "type": "done",
                        "answer": answer,
                        "citations": citations,
                        "warnings": warnings,
                        "suggestions": followups,
                        "conversation": conversation_id,
                    }
                )
            except Exception as exc:
                logging.exception("Chat failed")
                yield sse({"type": "error", "message": str(exc)[:400]})

    return StreamingResponse(
        keepalive(generate()),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/benchmarks", dependencies=[Depends(authorize)])
async def benchmarks() -> dict:
    path = settings.data_dir / "benchmark.json"
    return (
        json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"status": "not_run", "rows": []}
    )
