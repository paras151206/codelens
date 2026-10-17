import json
import sqlite3
from app.core.config import settings


def connect() -> sqlite3.Connection:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(settings.data_dir / "sessions.sqlite", timeout=20)
    db.execute("CREATE TABLE IF NOT EXISTS conversations (id TEXT PRIMARY KEY, repo TEXT, messages TEXT)")
    return db


def history(conversation: str, repo: str) -> list[dict]:
    with connect() as db:
        row = db.execute(
            "SELECT messages FROM conversations WHERE id=? AND repo=?", (conversation, repo)
        ).fetchone()
    return json.loads(row[0]) if row else []


def save(conversation: str, repo: str, messages: list[dict]) -> None:
    with connect() as db:
        db.execute(
            "INSERT INTO conversations VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE SET messages=excluded.messages WHERE conversations.repo=excluded.repo",
            (conversation, repo, json.dumps(messages[-20:])),
        )


def remove_repo(repo: str) -> None:
    with connect() as db:
        db.execute("DELETE FROM conversations WHERE repo=?", (repo,))
