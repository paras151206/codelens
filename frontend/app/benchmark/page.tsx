"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { motion } from "framer-motion";
import {
  ArrowLeft,
  ArrowRight,
  BarChart3,
  Braces,
  CheckCircle2,
  FlaskConical,
  RefreshCw,
  Terminal,
} from "lucide-react";
import { api } from "@/lib/api";
type Results = {
  status: string;
  generated?: string;
  questions?: number;
  k?: number;
  embedding_model?: string;
  reranker_model?: string;
  commits?: Record<string, string>;
  rows: { mode: string; recall: number; mrr: number; latency_ms: number }[];
};
export default function Benchmark() {
  const [data, setData] = useState<Results | null>(null),
    [error, setError] = useState("");
  function load() {
    setError("");
    api<Results>("/api/benchmarks")
      .then(setData)
      .catch((e) => setError(e.message));
  }
  useEffect(load, []);
  return (
    <div className="benchmark-page">
      <header className="topbar">
        <Link className="logo small" href="/">
          <span className="logo-mark">
            <Braces size={18} />
          </span>
          Code<span className="logo-light">Lens</span>
        </Link>
        <Link className="text-button" href="/" style={{ marginLeft: "auto" }}>
          <ArrowLeft size={14} />
          Back to your codebase
        </Link>
      </header>
      <main className="benchmark-main">
        <div className="eyebrow">MEASURED, NOT ASSUMED</div>
        <h1>Show your work.</h1>
        <p className="benchmark-intro">
          Retrieval quality, out in the open. Compare semantic search,
          <br />
          hybrid fusion, and cross-encoder reranking on real repositories.
        </p>
        <div className="benchmark-toolbar">
          <span>
            <FlaskConical size={15} /> REPOSITORY RETRIEVAL EVALUATION
          </span>
          <button className="text-button" onClick={load}>
            <RefreshCw size={13} />
            Refresh results
          </button>
        </div>
        {error ? (
          <div className="benchmark-empty">
            <Terminal size={30} />
            <h2>Connect to your backend</h2>
            <p>{error}</p>
            <Link href="/" className="text-button">
              Open connection settings on the home page <ArrowRight size={13} />
            </Link>
          </div>
        ) : !data ? (
          <div className="skeleton">Loading measured results…</div>
        ) : data.status !== "complete" ? (
          <div className="benchmark-empty">
            <BarChart3 size={35} />
            <h2>Good evidence takes a real run.</h2>
            <p>
              No benchmark has been run on this backend yet.
              <br />
              Run the evaluation command below to measure all three retrieval
              modes.
            </p>
            <code>
              cd backend
              <br />
              uv run python -m app.eval.run
            </code>
            <p className="muted">
              The harness indexes two public repositories and evaluates 32
              labeled questions.
              <br />
              No LLM key is required. Models are downloaded on the first run.
            </p>
          </div>
        ) : (
          <>
            <div className="benchmark-stats">
              <div>
                <strong>{data.questions}</strong>
                <span>labeled questions</span>
              </div>
              <div>
                <strong>2</strong>
                <span>public repositories</span>
              </div>
              <div>
                <strong>{data.k}</strong>
                <span>retrieved chunks per query</span>
              </div>
              <div>
                <strong>3</strong>
                <span>retrieval strategies</span>
              </div>
            </div>
            <div className="benchmark-charts">
              {(["recall", "mrr"] as const).map((metric) => (
                <section key={metric}>
                  <h2>
                    {metric === "recall"
                      ? `Recall @ ${data.k}`
                      : "Mean reciprocal rank"}
                  </h2>
                  <p>
                    {metric === "recall"
                      ? "Fraction of labeled source targets found in the top results."
                      : "How early the first relevant source appears."}
                  </p>
                  {data.rows.map((row, i) => (
                    <div className="benchmark-bar" key={row.mode}>
                      <div>
                        <span>{row.mode}</span>
                        <strong>{(row[metric] * 100).toFixed(1)}%</strong>
                      </div>
                      <div className="bar-track">
                        <motion.span
                          style={{
                            background: ["#63777c", "#9e91c4", "var(--accent)"][
                              i
                            ],
                          }}
                          initial={{ width: 0 }}
                          animate={{ width: `${row[metric] * 100}%` }}
                          transition={{ duration: 0.8, delay: i * 0.15 }}
                        />
                      </div>
                    </div>
                  ))}
                </section>
              ))}
            </div>
            <div className="benchmark-table">
              <table>
                <thead>
                  <tr>
                    <th>Strategy</th>
                    <th>Recall @{data.k}</th>
                    <th>MRR</th>
                    <th>Mean query latency</th>
                  </tr>
                </thead>
                <tbody>
                  {data.rows.map((row) => (
                    <tr key={row.mode}>
                      <td>{row.mode}</td>
                      <td>{row.recall.toFixed(3)}</td>
                      <td>{row.mrr.toFixed(3)}</td>
                      <td>{row.latency_ms.toFixed(0)} ms</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="benchmark-provenance">
              <CheckCircle2 size={17} />
              <div>
                <strong>
                  Measured on{" "}
                  {data.generated
                    ? new Date(data.generated).toLocaleString()
                    : "this backend"}
                </strong>
                <p>
                  Embedding model: {data.embedding_model}
                  <br />
                  Reranker: {data.reranker_model}
                </p>
                {Object.entries(data.commits || {}).map(([repo, sha]) => (
                  <p key={repo}>
                    {repo} · <code>{sha.slice(0, 12)}</code>
                  </p>
                ))}
              </div>
            </div>
          </>
        )}
        <section className="methodology">
          <h2>A small, reproducible baseline.</h2>
          <p>
            The dataset covers ItsDangerous and MarkupSafe. Each question labels
            a source file and symbol. A result counts as relevant only when a
            retrieved chunk covers that symbol. Retrieval uses the original
            question without LLM rewriting or context expansion, isolating the
            effect of each ranking strategy.
          </p>
          <p>
            These are development-set results, not a claim of universal
            accuracy. Latency depends on CPU, cache state, and model downloads.
            Inspect the JSON dataset, adjust the questions to your codebase, and
            rerun.
          </p>
        </section>
      </main>
    </div>
  );
}
