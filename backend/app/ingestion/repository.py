import json
import os
import re
import subprocess
import time
from pathlib import Path
from collections.abc import Callable
from app.core.config import settings
from app.ingestion.chunker import chunk_file

SKIP = {"node_modules", ".git", "dist", "build", "vendor", ".next", "__pycache__", ".venv", "coverage"}
EXT = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".svg",
    ".ico",
    ".pdf",
    ".zip",
    ".gz",
    ".mp4",
    ".woff",
    ".woff2",
    ".ttf",
    ".exe",
    ".dll",
    ".map",
}


def clone(url: str, target: Path) -> str:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_LFS_SKIP_SMUDGE": "1"}
    command = [
        "git",
        *(["-c", "http.sslBackend=openssl"] if os.name == "nt" else []),
        "-c",
        "core.hooksPath=/dev/null",
        "clone",
        "--depth",
        "1",
        "--no-tags",
        "--",
        url,
        str(target),
    ]
    with open(target.parent / "clone.log", "wb") as log:
        process = subprocess.Popen(command, stdout=log, stderr=log, env=env)
        started = time.monotonic()
        try:
            while process.poll() is None:
                if time.monotonic() - started > settings.clone_timeout:
                    raise ValueError("Clone exceeded the time limit")
                size = (
                    sum(p.stat().st_size for p in target.rglob("*") if p.is_file() and not p.is_symlink())
                    if target.exists()
                    else 0
                )
                if size > settings.max_repo_mb * 1024 * 1024:
                    raise ValueError("Repository exceeds the size limit")
                time.sleep(0.3)
            if process.returncode:
                raise ValueError("Could not clone this public repository; check its URL and availability")
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
    return subprocess.check_output(["git", "-C", str(target), "rev-parse", "HEAD"], text=True).strip()


def parse_repo(root: Path, progress: Callable) -> tuple[list, dict]:
    names = subprocess.check_output(
        ["git", "-C", str(root), "ls-files", "-z", "--cached", "--exclude-standard"], text=True
    ).split("\0")
    # Explicitly honor ignore rules even for tracked paths.
    ignored = subprocess.run(
        ["git", "-C", str(root), "check-ignore", "--no-index", "-z", "--stdin"],
        input="\0".join(filter(None, names)) + "\0",
        text=True,
        capture_output=True,
    ).stdout.split("\0")
    ignored_set = set(ignored)
    chunks, files, readme = [], {}, ""
    total = 0
    for name in names:
        path = root / name
        if not name or name in ignored_set or any(part in SKIP for part in path.relative_to(root).parts):
            continue
        if path.is_symlink() or not path.is_file() or root.resolve() not in path.resolve().parents:
            continue
        if path.suffix.lower() in EXT or path.name.endswith((".lock", "-lock.json", ".min.js")):
            continue
        if path.stat().st_size > settings.max_file_bytes:
            continue
        raw = path.read_bytes()
        if b"\0" in raw:
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        total += len(raw)
        if total > settings.max_repo_mb * 1024 * 1024 or len(files) >= settings.max_files:
            raise ValueError("Repository content exceeds configured limits")
        files[name] = text
        chunks.extend(chunk_file(name, text))
        if len(chunks) > settings.max_chunks:
            raise ValueError("Repository has too many chunks")
        if path.name.lower().startswith("readme") and not readme:
            readme = text[:6000]
        if len(files) % 20 == 0:
            progress("Parsing", files=len(files), chunks=len(chunks), message=f"Parsed {name}")
    if not chunks:
        raise ValueError("No supported text files found")
    links: set[tuple[str, str]] = set()
    stems: dict[str, list[str]] = {}
    for name in files:
        stems.setdefault(Path(name).stem, []).append(name)
    for chunk in chunks:
        for statement in chunk.imports:
            for token in re.findall(r"[\w]+", statement):
                for target in stems.get(token, []):
                    if target != chunk.path:
                        links.add((chunk.path, target))
    paragraphs = [
        p.strip()
        for p in re.split(r"\n\s*\n", readme)
        if p.strip() and not p.strip().startswith(("[![", "<", "```", ".. "))
    ]
    summary = "\n\n".join(paragraphs[:8])[:2500]
    metadata = {
        "files": list(files),
        "readme": readme,
        "readme_summary": summary,
        "graph": {
            "nodes": [{"id": n} for n in files],
            "links": [{"source": a, "target": b} for a, b in sorted(links)],
        },
    }
    (root.parent / "files.json").write_text(json.dumps(files), encoding="utf-8")
    return chunks, metadata
