import json
import uuid
from fastapi.testclient import TestClient
from app.api import main
from app.llm.providers import ProviderPool
from app.core.config import settings


def test_api_snapshot_file_access_and_auth(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "access_token", "test-token")
    monkeypatch.setattr(main, "pool", ProviderPool([]))
    repo = str(uuid.uuid4())
    directory = tmp_path / repo
    directory.mkdir()
    (directory / "metadata.json").write_text(json.dumps({"id": repo, "files": ["a.py"]}))
    (directory / "files.json").write_text(json.dumps({"a.py": "def hello(): return 1"}))
    with TestClient(main.app) as client:
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
    with TestClient(main.app) as client:
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
    with TestClient(main.app) as client:
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
    with TestClient(main.app) as client:
        assert client.post("/api/repos", json={"url": url}).json() == {"id": repo}
        assert client.get(f"/api/repos/{repo}/status").json()["events"][-1]["type"] == "done"
