import argparse
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from app.core.config import settings
from app.ingestion.repository import clone, parse_repo
from app.retrieval.engine import Index, embeddings


def relevant(chunk: dict, target: dict) -> bool:
    return (
        chunk["path"] == target["path"]
        and chunk["symbol"] == target["symbol"]
        and ("parent" not in target or chunk["parent"] == target["parent"])
    )


def metrics(chunks: list[dict], targets: list[dict]) -> tuple[float, float]:
    recall = sum(any(relevant(chunk, target) for chunk in chunks) for target in targets) / len(targets)
    rank = next((i for i, chunk in enumerate(chunks, 1) if any(relevant(chunk, t) for t in targets)), None)
    return recall, 1 / rank if rank else 0.0


def main() -> None:
    parser = argparse.ArgumentParser(description="Run CodeLens retrieval evaluation; no LLM calls")
    parser.add_argument("--k", type=int, default=5, choices=range(1, 25))
    args = parser.parse_args()
    if not settings.rerank_enabled or not settings.reranker_model:
        raise SystemExit("Enable RERANK_ENABLED and configure RERANKER_MODEL for the three-mode evaluation")
    dataset = json.loads(Path(__file__).with_name("questions.json").read_text(encoding="utf-8"))
    indexes, commits = {}, {}
    try:
        for name, info in dataset["repositories"].items():
            directory = settings.data_dir / "evaluation" / name
            directory.mkdir(parents=True, exist_ok=True)
            root = directory / "repo"
            if not root.exists():
                clone(info["url"], root)
            git = ["git", *(["-c", "http.sslBackend=openssl"] if os.name == "nt" else []), "-C", str(root)]
            current = subprocess.check_output([*git, "rev-parse", "HEAD"], text=True).strip()
            if current != info["commit"]:
                subprocess.run([*git, "fetch", "--depth", "1", "origin", info["commit"]], check=True)
                subprocess.run([*git, "checkout", "--detach", info["commit"]], check=True)
            commits[name] = info["commit"]
            fingerprint = directory / "fingerprint.json"
            expected = {"commit": info["commit"], "embedding": settings.embedding_model, "chunker": 1}
            if fingerprint.exists() and json.loads(fingerprint.read_text()) != expected:
                raise SystemExit(
                    f"Configuration changed. Move {directory} aside before rebuilding its index."
                )
            if (directory / "chunks.json").exists():
                indexes[name] = Index(directory)
            else:
                chunks, _ = parse_repo(
                    root, lambda stage, **data: print(f"{name}: {stage} {data}", flush=True)
                )
                indexes[name] = Index(
                    directory, chunks, lambda stage, **data: print(f"{name}: {stage} {data}", flush=True)
                )
                fingerprint.write_text(json.dumps(expected), encoding="utf-8")
            for question in [q for q in dataset["questions"] if q["repo"] == name]:
                for target in question["targets"]:
                    if not any(relevant(c.dict(), target) for c in indexes[name].chunks):
                        raise ValueError(f"Missing ground-truth target: {target}")
        rows, details = [], []
        # Warm downloads and initialization outside measured queries. Fail, never silently label hybrid as reranked.
        for index in indexes.values():
            warm = index.search(["serialization"], "hybrid+rerank", expand=False)
            if warm["warning"]:
                raise RuntimeError(warm["warning"])
            index.cache.clear()
        embeddings.encode([q["question"] for q in dataset["questions"]])
        for mode in ("dense", "hybrid", "hybrid+rerank"):
            values, durations = [], []
            for question in dataset["questions"]:
                index = indexes[question["repo"]]
                start = time.perf_counter()
                found = index.search([question["question"]], mode, expand=False)
                durations.append((time.perf_counter() - start) * 1000)
                if mode == "hybrid+rerank" and found["warning"]:
                    raise RuntimeError(found["warning"])
                chunks = [index.by_id[c["id"]].dict() for c in found["scores"][: args.k]]
                recall, mrr = metrics(chunks, question["targets"])
                values.append((recall, mrr))
                details.append(
                    {
                        "mode": mode,
                        **question,
                        "recall": recall,
                        "mrr": mrr,
                        "retrieved": [{k: c[k] for k in ("path", "symbol", "start", "end")} for c in chunks],
                    }
                )
            row = {
                "mode": mode,
                "recall": sum(v[0] for v in values) / len(values),
                "mrr": sum(v[1] for v in values) / len(values),
                "latency_ms": sum(durations) / len(durations),
            }
            rows.append(row)
            print(row, flush=True)
        result = {
            "status": "complete",
            "generated": datetime.now(timezone.utc).isoformat(),
            "questions": len(dataset["questions"]),
            "k": args.k,
            "embedding_model": settings.embedding_model,
            "reranker_model": settings.reranker_model,
            "commits": commits,
            "rows": rows,
            "details": details,
        }
        target = settings.data_dir / "benchmark.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(result, indent=2), encoding="utf-8")
        temporary.replace(target)
        print(f"\n| Mode | Recall@{args.k} | MRR | Latency (ms) |\n|---|---:|---:|---:|")
        for row in rows:
            print(f"| {row['mode']} | {row['recall']:.3f} | {row['mrr']:.3f} | {row['latency_ms']:.0f} |")
    finally:
        for index in indexes.values():
            index.client.close()


if __name__ == "__main__":
    main()
