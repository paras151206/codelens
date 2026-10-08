# Validation record — October 2, 2026

## Passed

- Backend: 28 tests, including all eight parser extensions, RRF, citation-range checks, Unicode citation normalization, provider/model fallback, URL validation, authenticated file access, traversal rejection, persistent conversations, abstention, and SSE keepalives.
- Backend lint: Ruff.
- Frontend: TypeScript validation and optimized Next.js production build.
- Actual local ingestion: `pallets/itsdangerous`, commit `672971d66a2ef9f85151e53283113f33d642dabd`, 46 files and 219 chunks.
- Actual hosted generation: a configured Groq provider produced a source-grounded answer about signing-key derivation. A rate-limit response during an earlier audit triggered a retry with model rediscovery. Cross-provider fallback is separately covered with deterministic test providers.
- Browser integration in headless Microsoft Edge: real repository ingestion, source loading in local Monaco, streamed answer, nine clickable citation references, source-line highlighting, retrieval X-ray, interactive graph canvas, conversation restore after reload, and measured benchmark rendering. No uncaught page errors.
- Browser UI: landing, command palette, accent selection, 390px mobile viewport with no horizontal overflow.
- One-command launcher: `node scripts/dev.mjs` successfully started both services.
- Evaluation: 32 validated source-symbol questions across pinned ItsDangerous and MarkupSafe snapshots. Full results: [benchmark-results.json](benchmark-results.json).

## Measured retrieval

| Mode | Recall@5 | MRR | Warm mean query latency |
|---|---:|---:|---:|
| Dense | 0.71875 | 0.42552 | 2.40 ms |
| Hybrid | 0.68750 | 0.50000 | 3.07 ms |
| Hybrid + rerank | 0.75000 | 0.54896 | 79.25 ms |

These timings include retrieval/reranking only, with query embeddings warmed equally and result caches initially empty. They exclude LLM calls and ingestion. The JSON report records exact model names, revisions, labels, per-question ranks, and generation time.

## Limits of this verification

- Gemini and Ollama were implemented against their HTTP protocols but not exercised against live credentials or a running Ollama service in this environment.
- Docker and remote hosting were not executed. Docker was not installed here. Hosted free-tier RAM and persistence are material constraints, described in the README.
- The bundled dependency versions produce one harmless Starlette TestClient deprecation warning; tests pass.
- Semantic claim checking is probabilistic. Unsupported claims and audit failures are exposed to the user; the application does not promise hallucination-free output.
- Screenshots document the local app and measured data. The landing preview is explicitly illustrative.
