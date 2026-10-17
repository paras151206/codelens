import json
import uuid
from fastapi.testclient import TestClient
from app.api import main
from app.llm.providers import ProviderPool
from app.core.config import settings

OWNER = "11111111-1111-4111-8111-111111111111"


def test_api_snapshot_file_access_and_auth(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "access_token", "test-token")
    monkeypatch.setattr(main, "pool", ProviderPool([]))
    repo = str(uuid.uuid4())
    directory = tmp_path / repo
    directory.mkdir()
    (directory / "metadata.json").write_text(json.dumps({"id": repo, "files": ["a.py"]}))
    (directory / "files.json").write_text(json.dumps({"a.py": "def hello(): return 1"}))
    (tmp_path / repo / "request.json").write_text(json.dumps({"owner": OWNER}))
    with TestClient(main.app) as client:
        client.headers["X-CodeLens-Session"] = OWNER
        assert client.get(f"/api/repos/{repo}").status_code == 401
        headers = {"Authorization": "Bearer test-token"}
        result = client.get(f"/api/repos/{repo}/file", params={"path": "a.py"}, headers=headers)
        assert result.json()["text"] == "def hello(): return 1"
        assert (
            client.get(f"/api/repos/{repo}/file", params={"path": "../../.env"}, headers=headers).status_code
            == 404
        )
        assert client.get("/api/repos/not-a-uuid", headers=headers).status_code == 404
        assert (
            client.post("/api/repos", json={"url": "http://127.0.0.1/internal"}, headers=headers).status_code
            == 422
        )


def test_chat_sse_abstains_when_evidence_is_weak(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "access_token", "")
    monkeypatch.setattr(main, "pool", ProviderPool([]))
    repo = str(uuid.uuid4())
    directory = tmp_path / repo
    directory.mkdir()
    (directory / "metadata.json").write_text(json.dumps({"id": repo, "files": ["a.py"], "readme": ""}))

    class WeakIndex:
        def search(self, queries):
            return {"confident": False, "chunks": [], "scores": [], "warning": None}

    async def index(_repo):
        return WeakIndex()

    monkeypatch.setattr(main, "get_index", index)
    (tmp_path / repo / "request.json").write_text(json.dumps({"owner": OWNER}))
    with TestClient(main.app) as client:
        client.headers["X-CodeLens-Session"] = OWNER
        response = client.post(
            f"/api/repos/{repo}/chat", json={"question": "Where are the imaginary unicorns?"}
        )
        events = [
            json.loads(line.removeprefix("data: "))
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        assert events[-1]["type"] == "done"
        assert "couldn't find" in events[-1]["answer"]
        assert len(events[-1]["suggestions"]) == 3
        conversation = events[-1]["conversation"]
        saved = client.get(f"/api/repos/{repo}/conversations/{conversation}").json()
        assert len(saved["messages"]) == 2


def test_indexing_status_detects_restart_and_reports_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "access_token", "")
    monkeypatch.setattr(main, "pool", ProviderPool([]))
    monkeypatch.setattr(main, "jobs", {})
    repo = str(uuid.uuid4())
    (tmp_path / repo).mkdir()
    (tmp_path / repo / "request.json").write_text(json.dumps({"owner": OWNER}))
    with TestClient(main.app) as client:
        client.headers["X-CodeLens-Session"] = OWNER
        result = client.get(f"/api/repos/{repo}/status").json()
        assert result["events"][-1]["type"] == "error"
        assert "restart" in result["events"][-1]["message"]
        main.jobs[repo] = [{"type": "progress", "stage": "Embedding", "embedded": 32}]
        assert client.get(f"/api/repos/{repo}/status").json()["events"][0]["embedded"] == 32
        main.jobs[repo].append({"type": "error", "message": "Failed"})
        assert client.get(f"/api/repos/{repo}/status").json()["events"][-1]["type"] == "error"


def test_reopening_completed_repository_reuses_slot(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "access_token", "")
    monkeypatch.setattr(settings, "embedding_model", "test")
    monkeypatch.setattr(settings, "max_repos", 1)
    monkeypatch.setattr(main, "pool", ProviderPool([]))
    repo = str(uuid.uuid4())
    (tmp_path / repo).mkdir()
    url = "https://github.com/pallets/itsdangerous"
    (tmp_path / repo / "metadata.json").write_text(json.dumps({"id": repo, "url": url, "embedding_runtime": "onnx-v1"}))
    (tmp_path / repo / "request.json").write_text(json.dumps({"owner": OWNER}))
    with TestClient(main.app) as client:
        client.headers["X-CodeLens-Session"] = OWNER
        assert client.post("/api/repos", json={"url": url}).json() == {"id": repo}
        assert client.get(f"/api/repos/{repo}/status").json()["events"][-1]["type"] == "done"


def test_visitors_are_isolated_and_can_free_their_own_slots(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "access_token", "gateway-secret")
    monkeypatch.setattr(settings, "embedding_model", "test")
    monkeypatch.setattr(settings, "max_repos", 1)
    monkeypatch.setattr(main, "pool", ProviderPool([]))
    monkeypatch.setattr(main, "jobs", {})
    monkeypatch.setattr(main, "indexes", {})
    other = "22222222-2222-4222-8222-222222222222"
    repo = str(uuid.uuid4())
    path = tmp_path / repo
    path.mkdir()
    url = "https://github.com/pallets/itsdangerous"
    meta = {"id": repo, "url": url, "name": "pallets/itsdangerous", "created": 1, "chunks": 1, "embedding_runtime": "onnx-v1"}
    (path / "metadata.json").write_text(json.dumps(meta))
    (path / "request.json").write_text(json.dumps({"url": url, "owner": OWNER}))
    (path / "files.json").write_text(json.dumps({"a.py": "secret workspace"}))
    main.store.save("conversation", repo, [{"content": "private question"}])

    async def fake_ingest(repo, url):
        main.jobs[repo].append({"type": "error", "message": "test only"})

    monkeypatch.setattr(main, "ingest", fake_ingest)
    with TestClient(main.app) as client:
        client.headers.update({"Authorization": "Bearer gateway-secret", "X-CodeLens-Session": other})
        assert client.get("/api/repos").json() == []
        for suffix in ("", "/status", "/events", "/file?path=a.py", "/conversations/conversation"):
            assert client.get(f"/api/repos/{repo}{suffix}").status_code == 404
        assert client.post(f"/api/repos/{repo}/chat", json={"question": "Show secrets"}).status_code == 404
        assert client.delete(f"/api/repos/{repo}").status_code == 404
        # The other user's full quota does not block this visitor, even for the same public URL.
        result = client.post("/api/repos", json={"url": url})
        assert result.status_code == 200
        assert result.json()["id"] != repo
        client.headers["X-CodeLens-Session"] = OWNER
        assert client.get("/api/repos").json()[0]["id"] == repo
        assert client.post("/api/repos", json={"url": "https://github.com/pallets/markupsafe"}).status_code == 429
        assert client.delete(f"/api/repos/{repo}").status_code == 200
        assert client.get("/api/repos").json() == []
        assert main.store.history("conversation", repo) == []
        assert client.post("/api/repos", json={"url": url}).status_code == 200


def test_missing_session_and_legacy_shared_repositories_are_inaccessible(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "access_token", "")
    monkeypatch.setattr(main, "pool", ProviderPool([]))
    repo = str(uuid.uuid4())
    (tmp_path / repo).mkdir()
    (tmp_path / repo / "metadata.json").write_text('{}')
    with TestClient(main.app) as client:
        assert client.get("/api/repos").status_code == 401
        client.headers["X-CodeLens-Session"] = OWNER
        assert client.get("/api/repos").json() == []
        assert client.get(f"/api/repos/{repo}").status_code == 404


def test_conversation_id_cannot_overwrite_another_repository(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    main.store.save("same-id", "owner-a-repo", [{"content": "original"}])
    main.store.save("same-id", "owner-b-repo", [{"content": "attacker"}])
    assert main.store.history("same-id", "owner-a-repo") == [{"content": "original"}]
    assert main.store.history("same-id", "owner-b-repo") == []


def test_ingestion_done_remains_terminal(tmp_path, monkeypatch):
    import asyncio
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(main, "jobs", {})
    monkeypatch.setattr(main, "clone", lambda *args: "commit")
    monkeypatch.setattr(main, "parse_repo", lambda *args: ([object()], {"files": ["a.py"]}))
    class FakeIndex:
        def __init__(self, path, chunks, progress):
            self.client = self
            progress("Embedding", embedded=1)
        def close(self):
            pass
    monkeypatch.setattr(main, "Index", FakeIndex)
    repo = str(uuid.uuid4())
    (tmp_path / repo).mkdir()
    main.jobs[repo] = []
    async def run():
        await main.ingest(repo, "https://github.com/octocat/Hello-World")
        await asyncio.sleep(0)
        assert main.jobs[repo][-1]["type"] == "done"
    asyncio.run(run())
