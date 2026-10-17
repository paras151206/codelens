import asyncio
import hmac
import json
import logging
import time
import uuid
import shutil
import threading
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
chat_slots = asyncio.Semaphore(1 if settings.low_memory else 2)
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
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type", "Authorization"],
)


async def authorize(request: Request) -> None:
    if settings.access_token and not hmac.compare_digest(
        request.headers.get("authorization", ""), f"Bearer {settings.access_token}"
    ):
        raise HTTPException(401, "Access token required")
    owner = request.headers.get("x-codelens-session", "")
    try:
        owner = str(uuid.UUID(owner))
    except ValueError:
        raise HTTPException(401, "Private session required. Reload CodeLens.") from None
    request.state.owner = owner
    repo = request.path_params.get("repo")
    if repo and owner_of(directory(repo)) != owner:
        raise HTTPException(404, "Repository not found in this browser session")
    host = owner
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


def owner_of(path: Path) -> str | None:
    request_file = path / "request.json"
    if not request_file.exists():
        return None  # Never expose legacy shared indexes to new visitors.
    return json.loads(request_file.read_text()).get("owner")


def metadata(repo: str) -> dict:
    path = directory(repo) / "metadata.json"
    if not path.exists():
        raise HTTPException(409, "Repository has not finished indexing")
    return json.loads(path.read_text(encoding="utf-8"))


async def get_index(repo: str) -> Index:
    meta = metadata(repo)
    if (meta.get("embedding_model") != settings.embedding_model or meta.get("embedding_runtime") != "onnx-v1"):
        raise HTTPException(409, "Embedding model changed. Reindex this repository with the current model.")
    # Called under index_lock by chat: only one loaded repository is retained.
    # Per-visitor disk quotas must not turn into an unbounded process-memory cache.
    if repo not in indexes:
        for old in indexes.values():
            old.client.close()
        indexes.clear()
        indexes[repo] = await asyncio.to_thread(Index, directory(repo))
    return indexes[repo]


def sse(event: dict) -> str:
    return "data: " + json.dumps(event) + "\n\n"


async def ingest(repo: str, url: str) -> None:
    loop = asyncio.get_running_loop()
    loop_thread = threading.get_ident()

    def progress(stage: str, **data) -> None:
        event = {"type": "progress", "stage": stage, **data}
        if threading.get_ident() == loop_thread:
            jobs[repo].append(event)
        else:
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
            index.client.close()
            progress("Indexing", message="Saving vector, keyword, and dependency indexes")
            meta.update(
                id=repo,
                url=url,
                commit=commit,
                name=url.removeprefix("https://github.com/"),
                chunks=len(chunks),
                created=time.time(),
                embedding_model=settings.embedding_model,
                embedding_runtime="onnx-v1",
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
async def repos(request: Request) -> list[dict]:
    results = []
    for path in settings.data_dir.glob("*/metadata.json"):
        if owner_of(path.parent) != request.state.owner:
            continue
        meta = json.loads(path.read_text(encoding="utf-8"))
        results.append({k: meta[k] for k in ("id", "name", "url", "created", "chunks")})
    return sorted(results, key=lambda r: r["created"], reverse=True)


@app.post("/api/repos", dependencies=[Depends(authorize)])
async def create_repo(body: IngestRequest, request: Request) -> dict:
    owner = request.state.owner
    if not settings.embedding_model:
        raise HTTPException(503, "Configure EMBEDDING_MODEL in backend/.env first")
    # Reopening an indexed or active URL must not consume another repository slot.
    for item in settings.data_dir.glob("*/metadata.json"):
        if owner_of(item.parent) != owner:
            continue
        meta = json.loads(item.read_text(encoding="utf-8"))
        if meta.get("url") == body.url and meta.get("embedding_runtime") == "onnx-v1":
            return {"id": meta["id"]}
    for repo, history in jobs.items():
        request_file = settings.data_dir / repo / "request.json"
        if history and history[-1]["type"] not in {"done", "error"} and request_file.exists() and owner_of(request_file.parent) == owner:
            if json.loads(request_file.read_text())["url"] == body.url:
                return {"id": repo}
    if (
        sum(owner_of(p.parent) == owner for p in settings.data_dir.glob("*/metadata.json"))
        + sum(bool(h) and h[-1]["type"] not in {"done", "error"} and not (settings.data_dir / r / "metadata.json").exists() and owner_of(settings.data_dir / r) == owner for r, h in jobs.items())
        >= settings.max_repos
    ):
        raise HTTPException(
            429, "Your workspace is full. Return home and remove a repository to make room."
        )
    if sum((not events or events[-1]["type"] not in {"done", "error"}) and not (settings.data_dir / r / "metadata.json").exists() for r, events in jobs.items()) >= 2:
        raise HTTPException(429, "Two repositories are already queued; try again shortly")
    repo = str(uuid.uuid4())
    (settings.data_dir / repo).mkdir(parents=True)
    (settings.data_dir / repo / "request.json").write_text(json.dumps({"url": body.url, "owner": owner}))
    jobs[repo] = [{"type": "progress", "stage": "Cloning", "message": "Queued for indexing"}]
    task = asyncio.create_task(ingest(repo, body.url))
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    return {"id": repo}


@app.delete("/api/repos/{repo}", dependencies=[Depends(authorize)])
async def remove_repo(repo: str) -> dict:
    path = directory(repo)
    history = jobs.get(repo, [])
    if history and history[-1]["type"] not in {"done", "error"} and not (path / "metadata.json").exists():
        raise HTTPException(409, "Wait for indexing to finish before removing this repository")
    # Chat holds this semaphore while reading/saving, so deletion cannot race a response.
    async with chat_slots:
        async with index_lock:
            index = indexes.pop(repo, None)
            if index:
                index.client.close()
            store.remove_repo(repo)
            shutil.rmtree(path)
            jobs.pop(repo, None)
    return {"removed": True}


@app.get("/api/repos/{repo}/status", dependencies=[Depends(authorize)])
async def job_status(repo: str) -> dict:
    path = directory(repo)
    if (path / "metadata.json").exists():
        return {"events": [{"type": "done", "repo": metadata(repo)}]}
    if repo in jobs:
        return {"events": list(jobs[repo])}
    return {"events": [{"type": "error", "message": "Indexing was interrupted by a server restart. Please retry the repository."}]}


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
                async with index_lock:
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
