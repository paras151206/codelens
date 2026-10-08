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
