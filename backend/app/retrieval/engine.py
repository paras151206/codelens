import hashlib
import json
import re
import threading
from collections import defaultdict, OrderedDict
import numpy as np
from app.core.config import settings
from app.models.schema import Chunk


def tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", re.sub(r"([a-z])([A-Z])", r"\1 \2", text).lower())


def rrf(rankings: list[list[str]], k: int = 60) -> list[tuple[str, float]]:
    scores: dict[str, float] = defaultdict(float)
    for ranking in rankings:
        for rank, key in enumerate(dict.fromkeys(ranking), 1):
            scores[key] += 1 / (k + rank)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))


class Embeddings:
    def __init__(self) -> None:
        self.model = None
        self.lock = threading.RLock()

    def encode(self, texts: list[str]) -> np.ndarray:
        with self.lock:
            if not settings.embedding_model:
                raise ValueError("Set EMBEDDING_MODEL in backend/.env; see .env.example")
            if self.model is None:
                from sentence_transformers import SentenceTransformer

                self.model = SentenceTransformer(
                    settings.embedding_model,
                    device="cpu",
                    trust_remote_code=False,
                    cache_folder=str(settings.data_dir / "models"),
                )
            directory = settings.data_dir / "embeddings"
            directory.mkdir(parents=True, exist_ok=True)
            paths = [
                directory / (hashlib.sha256((settings.embedding_model + t).encode()).hexdigest() + ".npy")
                for t in texts
            ]
            missing = [i for i, p in enumerate(paths) if not p.exists()]
            if missing:
                vectors = self.model.encode(
                    [texts[i] for i in missing], batch_size=16, normalize_embeddings=True
                )
                for i, vector in zip(missing, vectors):
                    np.save(paths[i], vector)
            return np.stack([np.load(p, allow_pickle=False) for p in paths])


embeddings = Embeddings()


class Index:
    def __init__(self, directory, chunks: list[Chunk] | None = None, progress=None) -> None:
        from qdrant_client import QdrantClient, models
        from rank_bm25 import BM25Okapi

        self.directory = directory
        self.lock = threading.RLock()
        self.cache: OrderedDict = OrderedDict()
        self.ranker = None
        self.chunks = chunks or [
            Chunk(**c) for c in json.loads((directory / "chunks.json").read_text(encoding="utf-8"))
        ]
        self.by_id = {c.id: c for c in self.chunks}
        self.bm25 = BM25Okapi([tokens(c.contextual) for c in self.chunks])
        self.client = QdrantClient(path=str(directory / "vectors"))
        if chunks is not None:
            vectors = []
            for start in range(0, len(chunks), 32):
                vectors.extend(embeddings.encode([c.contextual for c in chunks[start : start + 32]]))
                if progress:
                    progress(
                        "Embedding",
                        embedded=min(start + 32, len(chunks)),
                        chunks=len(chunks),
                        message="Encoding code locally on CPU",
                    )
            self.client.create_collection(
                "code",
                vectors_config=models.VectorParams(size=len(vectors[0]), distance=models.Distance.COSINE),
            )
            for start in range(0, len(chunks), 128):
                self.client.upsert(
                    "code",
                    points=[
                        models.PointStruct(id=c.id, vector=v.tolist())
                        for c, v in zip(chunks[start : start + 128], vectors[start : start + 128])
                    ],
                )
            (directory / "chunks.json").write_text(json.dumps([c.dict() for c in chunks]), encoding="utf-8")

    def search(self, queries: list[str], mode: str = "hybrid+rerank", expand: bool = True) -> dict:
        with self.lock:
            return self._search(queries, mode, expand)

    def _search(self, queries: list[str], mode: str, expand: bool) -> dict:
        key = json.dumps([queries, mode, expand])
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        rankings, dense, sparse = [], {}, {}
        for query, vector in zip(queries, embeddings.encode(queries)):
            hits = self.client.query_points("code", query=vector.tolist(), limit=24).points
            ranking = []
            for hit in hits:
                cid = str(hit.id).replace("-", "")
                ranking.append(cid)
                dense[cid] = max(dense.get(cid, -1), hit.score)
            rankings.append(ranking)
            if mode != "dense":
                scores = self.bm25.get_scores(tokens(query))
                ids = np.argsort(scores)[::-1][:24]
                ranking = []
                for i in ids:
                    if scores[i] > 0:
                        cid = self.chunks[i].id
                        ranking.append(cid)
                        sparse[cid] = max(sparse.get(cid, 0), float(scores[i]))
                rankings.append(ranking)
        fused = rrf(rankings)[:24]
        ranked = [cid for cid, _ in fused]
        reranked, warning = {}, None
        if mode == "hybrid+rerank" and settings.rerank_enabled:
            try:
                from flashrank import Ranker, RerankRequest

                if not settings.reranker_model:
                    raise ValueError("RERANKER_MODEL is not configured")
                if self.ranker is None:
                    self.ranker = Ranker(
                        model_name=settings.reranker_model, cache_dir=str(settings.data_dir / "reranker")
                    )
                results = self.ranker.rerank(
                    RerankRequest(
                        query=queries[0],
                        passages=[{"id": cid, "text": self.by_id[cid].contextual} for cid in ranked],
                    )
                )
                ranked = [row["id"] for row in results]
                reranked = {row["id"]: float(row["score"]) for row in results}
            except Exception as exc:
                warning = f"Reranking unavailable ({type(exc).__name__}); hybrid ranking used"
        chosen = ranked[:6]
        selected = [self.by_id[cid] for cid in chosen]
        if expand:
            related = []
            for c in selected:
                for other in self.chunks:
                    nearby = other.path == c.path and (
                        abs(other.start - c.end) < 15 or (c.parent and other.symbol == c.parent)
                    )
                    imported = other.path != c.path and any(
                        other.path.rsplit("/", 1)[-1].split(".")[0] in statement for statement in c.imports
                    )
                    if (
                        (nearby or imported)
                        and other.id not in chosen
                        and other.id not in {r.id for r in related}
                    ):
                        related.append(other)
            selected += related[:4]
        confident = (
            max(dense.values(), default=0) >= settings.min_dense_score
            or max(sparse.values(), default=0) > 2.5
        )
        result = {
            "chunks": [c.dict() for c in selected],
            "confident": confident,
            "warning": warning,
            "scores": [
                {
                    "id": cid,
                    "path": self.by_id[cid].path,
                    "start": self.by_id[cid].start,
                    "end": self.by_id[cid].end,
                    "dense": dense.get(cid, 0),
                    "bm25": sparse.get(cid, 0),
                    "rrf": dict(fused)[cid],
                    "rerank": reranked.get(cid),
                    "chosen": cid in chosen,
                    "reason": "Top evidence after fusion and optional reranking"
                    if cid in chosen
                    else "Candidate",
                }
                for cid in ranked
            ],
        }
        self.cache[key] = result
        while len(self.cache) > settings.query_cache_size:
            self.cache.popitem(last=False)
        return result
