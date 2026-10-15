"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import {
  ArrowDownToLine,
  ArrowRight,
  ArrowUp,
  AudioLines,
  Braces,
  Check,
  ChevronRight,
  Code2,
  Command,
  ExternalLink,
  FileCode2,
  Folder,
  GitBranch,
  Github,
  Layers3,
  LoaderCircle,
  Network,
  Plus,
  Search,
  Settings2,
  ShieldCheck,
  Sparkles,
  Square,
  Terminal,
  X,
} from "lucide-react";
import Link from "next/link";
import dynamic from "next/dynamic";
import { Panel, PanelGroup, PanelResizeHandle } from "react-resizable-panels";
import { toast } from "sonner";
import {
  api,
  stream,
  type Citation,
  type Repo,
  type Retrieval,
  type StreamEvent,
} from "@/lib/api";
import Markdown from "./Markdown";
import { useKeyboardShortcut } from "@/hooks/useKeyboardShortcut";
const CodeViewer = dynamic(() => import("./CodeViewer"), { ssr: false });
const RepoGraph = dynamic(() => import("./RepoGraph"), { ssr: false });
type Message = {
  role: "user" | "assistant";
  content: string;
  citations?: Citation[];
  warnings?: string[];
  retrieval?: Retrieval;
};
const steps = ["Cloning", "Parsing", "Chunking", "Embedding", "Indexing"];
const examples = [
  "How is this project organized?",
  "Walk me through the main entry point",
  "Where is error handling implemented?",
];
function Logo({ small = false }: { small?: boolean }) {
  return (
    <span className={`logo ${small ? "small" : ""}`}>
      <span className="logo-mark">
        <Braces size={small ? 18 : 23} />
      </span>
      Code<span className="logo-light">Lens</span>
    </span>
  );
}
function Dot() {
  return <span className="status-dot" />;
}

export default function CodeLens() {
  const [screen, setScreen] = useState<"home" | "ingest" | "workspace">("home");
  const [url, setUrl] = useState(""),
    [repo, setRepo] = useState<Repo | null>(null),
    [recent, setRecent] = useState<Repo[]>([]);
  const [progress, setProgress] = useState<StreamEvent[]>([]),
    [error, setError] = useState("");
  const [messages, setMessages] = useState<Message[]>([]),
    [question, setQuestion] = useState(""),
    [busy, setBusy] = useState(false),
    [status, setStatus] = useState("");
  const [conversation, setConversation] = useState(""),
    [followups, setFollowups] = useState(examples),
    [retrieval, setRetrieval] = useState<Retrieval | null>(null);
  const [file, setFile] = useState<{ path: string; text: string } | null>(null),
    [citation, setCitation] = useState<Citation>(),
    [fileLoading, setFileLoading] = useState(false);
  const [search, setSearch] = useState(""),
    [tab, setTab] = useState<"code" | "graph" | "xray">("code");
  const [palette, setPalette] = useState(false),
    [settingsOpen, setSettingsOpen] = useState(false);
  const [provider, setProvider] = useState(""),
    [mobilePane, setMobilePane] = useState("chat");
  const [connection, setConnection] = useState("Connecting…");
  const abort = useRef<AbortController | null>(null),
    end = useRef<HTMLDivElement>(null),
    input = useRef<HTMLTextAreaElement>(null),
    activeFile = useRef(0);
  const current = progress[progress.length - 1];

  const refreshRecent = useCallback(() => {
    api<Repo[]>("/api/repos")
      .then(setRecent)
      .catch(() => {});
  }, []);
  const loadRepo = useCallback(async (id: string) => {
    try {
      const found = await api<Repo>(`/api/repos/${id}`);
      setRepo(found);
      setScreen("workspace");
      setMessages([]);
      setConversation("");
      setFile(null);
      setRetrieval(null);
      setFollowups(examples);
      window.history.replaceState(null, "", `/?repo=${id}`);
      const saved = localStorage.getItem(`codelens-conversation-${id}`);
      if (saved) {
        const history = await api<{ messages: Message[] }>(
          `/api/repos/${id}/conversations/${saved}`,
        );
        setConversation(saved);
        setMessages(history.messages);
      }
    } catch (e) {
      toast.error((e as Error).message);
    }
  }, []);
  useEffect(() => {
    refreshRecent();
    const id = new URLSearchParams(window.location.search).get("repo");
    if (id) void loadRepo(id);
    api<{ embedding_configured: boolean; providers: Record<string, unknown> }>(
      "/api/health",
    )
      .then((h) =>
        setConnection(
          !h.embedding_configured
            ? "Service temporarily unavailable"
            : Object.keys(h.providers).length
              ? "Ready"
              : "Chat is being set up",
        ),
      )
      .catch(() => setConnection("Service is waking up. Please try again shortly."));
    return () => abort.current?.abort();
  }, [loadRepo, refreshRecent]);
  useKeyboardShortcut("k", () => setPalette((x) => !x), true);
  useKeyboardShortcut("Escape", () => {
    setPalette(false);
    setSettingsOpen(false);
  });
  useKeyboardShortcut("/", () => input.current?.focus(), true);
  useEffect(() => {
    end.current?.scrollIntoView({
      behavior: busy ? "instant" : "smooth",
      block: "end",
    });
  }, [messages, status, busy]);
  async function ingest(target = url) {
    if (!/^https:\/\/github\.com\/[\w.-]+\/[\w.-]+\/?$/.test(target.trim())) {
      toast.error("Enter a GitHub URL: https://github.com/owner/repository");
      return;
    }
    setError("");
    setProgress([]);
    setScreen("ingest");
    abort.current = new AbortController();
    try {
      const created = await api<{ id: string }>("/api/repos", {
        method: "POST",
        body: JSON.stringify({ url: target.trim() }),
      });
      const deadline = Date.now() + 12 * 60_000;
      let completed = false;
      while (Date.now() < deadline && !abort.current.signal.aborted) {
        const result = await api<{ events: StreamEvent[] }>(`/api/repos/${created.id}/status`, {
          signal: AbortSignal.any([abort.current.signal, AbortSignal.timeout(90_000)]),
        });
        setProgress(result.events);
        for (const event of result.events) {
          if (event.type === "error") throw new Error(event.message || "Indexing failed. Please retry.");
          if (event.type === "done" && event.repo) {
            setRepo(event.repo);
            setScreen("workspace");
            setMessages([]);
            setConversation("");
            setFile(null);
            setRetrieval(null);
            setFollowups(examples);
            window.history.replaceState(null, "", `/?repo=${created.id}`);
            refreshRecent();
            toast.success("Your codebase is ready to explore");
          }
          if (event.type === "done") completed = true;
        }
        if (completed) break;
        await new Promise((resolve) => setTimeout(resolve, 4000));
      }
      if (!completed && !abort.current.signal.aborted) throw new Error("Indexing is taking too long. Please retry in a moment.");
    } catch (e) {
      if ((e as Error).name !== "AbortError") setError((e as Error).message);
    }
  }
  async function openFile(c: Citation) {
    if (!repo) return;
    const request = ++activeFile.current;
    setCitation(c);
    setTab("code");
    setMobilePane("inspect");
    setFileLoading(true);
    try {
      const opened = await api<{ path: string; text: string }>(
        `/api/repos/${repo.id}/file?path=${encodeURIComponent(c.path)}`,
      );
      if (request === activeFile.current) setFile(opened);
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      if (request === activeFile.current) setFileLoading(false);
    }
  }
  async function ask(value = question) {
    if (!repo || busy || !value.trim()) return;
    const content = value.trim();
    setQuestion("");
    setBusy(true);
    setStatus("Finding the right context");
    setMobilePane("chat");
    setMessages((p) => [
      ...p,
      { role: "user", content },
      { role: "assistant", content: "" },
    ]);
    abort.current = new AbortController();
    const update = (fn: (m: Message) => Message) =>
      setMessages((p) => p.map((m, i) => (i === p.length - 1 ? fn(m) : m)));
    try {
      await stream(
        `/api/repos/${repo.id}/chat`,
        (event) => {
          if (event.type === "token") {
            update((m) => ({ ...m, content: m.content + event.text }));
            setStatus("Reading the code with you");
          }
          if (event.type === "reset") {
            update((m) => ({ ...m, content: "" }));
            setStatus(event.reason || "Switching providers");
          }
          if (event.type === "status") setStatus(event.message || "Working");
          if (event.type === "provider")
            setProvider(`${event.provider} / ${event.model}`);
          if (event.type === "retrieval") {
            const result = event as unknown as Retrieval;
            setRetrieval(result);
            update((m) => ({ ...m, retrieval: result }));
          }
          if (event.type === "done") {
            update((m) => ({
              ...m,
              content: event.answer || "",
              citations: event.citations,
              warnings: event.warnings,
            }));
            setConversation(event.conversation || "");
            if (event.conversation)
              localStorage.setItem(
                `codelens-conversation-${repo.id}`,
                event.conversation,
              );
            setFollowups(event.suggestions || examples);
            setStatus("Source checks complete");
          }
        },
        { question: content, conversation },
        abort.current.signal,
      );
    } catch (e) {
      if ((e as Error).name !== "AbortError") {
        toast.error((e as Error).message);
        update((m) => ({
          ...m,
          warnings: [...(m.warnings || []), (e as Error).message],
        }));
      } else
        update((m) => ({
          ...m,
          warnings: [
            "Generation stopped. This partial answer has not been verified.",
          ],
        }));
    } finally {
      setBusy(false);
    }
  }
  function home() {
    abort.current?.abort();
    setScreen("home");
    setError("");
    window.history.replaceState(null, "", "/");
    refreshRecent();
  }
  async function copyShare() {
    await navigator.clipboard.writeText(
      `${window.location.origin}/?repo=${repo?.id}`,
    );
    toast.success(
      "Repository link copied. Recipients need access to this backend.",
    );
  }
  function exportChat() {
    const text =
      `# CodeLens · ${repo?.name}\n\nSnapshot: ${repo?.commit}\n\n` +
      messages
        .map(
          (m) =>
            `## ${m.role === "user" ? "You" : "CodeLens"}\n\n${m.content}${m.warnings?.length ? "\n\n> Verification notes: " + m.warnings.join("; ") : ""}`,
        )
        .join("\n\n");
    const url = URL.createObjectURL(
      new Blob([text], { type: "text/markdown" }),
    );
    const a = document.createElement("a");
    a.href = url;
    a.download = "codelens-conversation.md";
    a.click();
    URL.revokeObjectURL(url);
    toast.success("Conversation exported");
  }
  const fileTree = (
    <>
      <div className="pane-heading">
        <span>
          <Folder size={14} /> EXPLORER
        </span>
        <span className="count">{repo?.files.length}</span>
      </div>
      <div className="file-search">
        <Search size={14} />
        <input
          aria-label="Search files"
          placeholder="Find a file…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <kbd>⌘ K</kbd>
      </div>
      <div className="tree-root">
        <GitBranch size={13} />
        {repo?.name.split("/")[1]}
        <span className="branch">{repo?.commit.slice(0, 7)}</span>
      </div>
      <div className="file-list">
        {repo?.files
          .filter((p) => p.toLowerCase().includes(search.toLowerCase()))
          .map((path) => (
            <button
              title={path}
              className={file?.path === path ? "active" : ""}
              key={path}
              onClick={() => openFile({ path, start: 1, end: 1 })}
            >
              <FileCode2 size={14} />
              <span>{path}</span>
            </button>
          ))}
        {repo &&
          !repo.files.some((p) =>
            p.toLowerCase().includes(search.toLowerCase()),
          ) && <p className="muted p-4">No matching files</p>}
      </div>
      <div className="index-summary">
        <span>
          <Dot /> Snapshot indexed
        </span>
        <small>
          {repo?.chunks.toLocaleString()} code chunks · local embeddings
        </small>
      </div>
    </>
  );
  const inspect = (
    <>
      <div className="inspect-tabs">
        {(
          [
            { id: "code", icon: Code2, label: "Source" },
            { id: "graph", icon: Network, label: "Graph" },
            { id: "xray", icon: AudioLines, label: "X-ray" },
          ] as const
        ).map((t) => (
          <button
            key={t.id}
            className={tab === t.id ? "selected" : ""}
            onClick={() => setTab(t.id)}
          >
            <t.icon size={14} />
            {t.label}
          </button>
        ))}
      </div>
      {tab === "code" ? (
        fileLoading ? (
          <div className="skeleton source-skeleton">Loading source…</div>
        ) : file ? (
          <>
            <div className="source-title">
              <FileCode2 size={13} />
              <span>{file.path}</span>
              <a
                aria-label="Open file on GitHub"
                href={`${repo?.url}/blob/${repo?.commit}/${file.path}#L${citation?.start || 1}`}
                target="_blank"
                rel="noreferrer"
              >
                <ExternalLink size={13} />
              </a>
            </div>
            <div className="editor">
              <CodeViewer file={file} citation={citation} />
            </div>
            <div className="source-footer">
              <ShieldCheck size={12} /> Read-only repository snapshot{" "}
              <span>UTF-8</span>
            </div>
          </>
        ) : (
          <div className="pane-empty">
            <div className="empty-icon">
              <Code2 size={26} />
            </div>
            <h3>The source of truth.</h3>
            <p>
              Open a file or follow a citation.
              <br />
              See the exact code behind every answer.
            </p>
            <span className="mini-label">
              CONTEXT, WITHOUT THE CONTEXT SWITCH.
            </span>
          </div>
        )
      ) : tab === "graph" && repo ? (
        <>
          <div className="graph-caption">
            Import relationships · cited files glow
            <br />
            <small>Heuristic graph · click a node to inspect</small>
          </div>
          <RepoGraph
            key={repo.id}
            repo={repo}
            cited={messages.flatMap(
              (m) => m.citations?.map((c) => c.path) || [],
            )}
            open={(path) => openFile({ path, start: 1, end: 1 })}
          />
        </>
      ) : (
        <div className="xray">
          {retrieval ? (
            <>
              <div className="eyebrow">RETRIEVAL X-RAY</div>
              <h3>Behind the answer</h3>
              <div className="intent">{retrieval.intent}</div>
              <h4>Search perspectives</h4>
              {retrieval.queries.map((q, i) => (
                <div className="subquery" key={q}>
                  <span>0{i + 1}</span>
                  {q}
                </div>
              ))}
              {retrieval.warning && (
                <div className="warning">{retrieval.warning}</div>
              )}
              <div className="score-legend">
                <span>● Dense</span>
                <span>● BM25</span>
                <span>● Rerank</span>
              </div>
              {retrieval.scores.slice(0, 12).map((score, i) => (
                <button
                  className="score-card"
                  key={score.id}
                  onClick={() => openFile(score)}
                >
                  <div>
                    <span>
                      {String(i + 1).padStart(2, "0")} ·{" "}
                      {score.path.split("/").pop()}
                    </span>
                    {score.chosen && <Check size={12} />}
                  </div>
                  {(["dense", "bm25", "rerank"] as const).map((kind, j) => (
                    <div className={`score-track score-${j}`} key={kind}>
                      <motion.span
                        initial={{ width: 0 }}
                        animate={{
                          width: `${Math.max(0, Math.min(100, (score[kind] || 0) * (kind === "bm25" ? 5 : 100)))}%`,
                        }}
                      />
                      <small>{score[kind]?.toFixed(3) ?? "off"}</small>
                    </div>
                  ))}
                  <small>
                    {score.reason} · RRF {score.rrf.toFixed(4)}
                  </small>
                </button>
              ))}
            </>
          ) : (
            <div className="pane-empty">
              <AudioLines size={30} />
              <h3>Retrieval, made visible.</h3>
              <p>
                Ask a question to inspect search queries, scores, and the
                evidence selected.
              </p>
            </div>
          )}
        </div>
      )}
    </>
  );
  const chat = (
    <>
      <div className="chat-heading">
        <div>
          <Sparkles size={16} />
          <span>Ask your codebase</span>
          <span className="beta-tag">AI</span>
        </div>
        <button
          className="icon-button"
          aria-label="Export chat as Markdown"
          disabled={!messages.length}
          onClick={exportChat}
        >
          <ArrowDownToLine size={16} />
        </button>
      </div>
      <div className="messages">
        {messages.length === 0 ? (
          <motion.div
            className="chat-empty"
            initial={{ opacity: 0, y: 15 }}
            animate={{ opacity: 1, y: 0 }}
          >
            <div className="orbit-logo">
              <Braces size={28} />
            </div>
            <div className="eyebrow">YOUR REPO. A NEW PERSPECTIVE.</div>
            <h2>Let’s connect the dots.</h2>
            <p>
              From the big picture to a single function.
              <br />
              Ask anything. Follow the evidence.
            </p>
            <div className="starter-list">
              {examples.map((q, i) => (
                <button key={q} onClick={() => ask(q)}>
                  <span>
                    {i === 0 ? (
                      <Layers3 size={16} />
                    ) : i === 1 ? (
                      <GitBranch size={16} />
                    ) : (
                      <Code2 size={16} />
                    )}
                  </span>
                  {q}
                  <ArrowUp size={14} />
                </button>
              ))}
            </div>
          </motion.div>
        ) : (
          messages.map((message, i) => (
            <motion.article
              initial={{ opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              className={`message ${message.role}`}
              key={i}
            >
              <div className="message-label">
                {message.role === "user" ? (
                  <>
                    <span className="avatar">Y</span>You
                  </>
                ) : (
                  <>
                    <span className="avatar ai">
                      <Braces size={14} />
                    </span>
                    CodeLens{" "}
                    <span className="muted">/ grounded in your code</span>
                  </>
                )}
              </div>
              {message.content ? (
                <Markdown text={message.content} open={openFile} />
              ) : busy && i === messages.length - 1 ? (
                <div className="thinking">
                  <span />
                  <span />
                  <span />
                </div>
              ) : null}
              {message.warnings?.map((warning, j) => (
                <div className="warning" key={j}>
                  <ShieldCheck size={14} />
                  {warning}
                </div>
              ))}
              {message.retrieval && !busy && (
                <button
                  className="text-button evidence-button"
                  onClick={() => {
                    setRetrieval(message.retrieval!);
                    setTab("xray");
                    setMobilePane("inspect");
                  }}
                >
                  <AudioLines size={13} /> Inspect retrieval{" "}
                  <ChevronRight size={12} />
                </button>
              )}
            </motion.article>
          ))
        )}
        <div ref={end} />
      </div>
      <div className="composer-area">
        {messages.length > 0 && !busy && (
          <div className="followups">
            {followups.map((q) => (
              <button key={q} onClick={() => ask(q)}>
                {q}
                <ArrowUp size={11} />
              </button>
            ))}
          </div>
        )}
        <form
          className="composer"
          onSubmit={(e) => {
            e.preventDefault();
            void ask();
          }}
        >
          <textarea
            ref={input}
            aria-label="Ask about this repository"
            placeholder="Ask a question about this codebase…"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            rows={2}
            maxLength={3000}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void ask();
              }
            }}
          />
          <div className="composer-bottom">
            <span>
              <AtRepo /> {repo?.name.split("/")[1]}
            </span>
            {busy ? (
              <button
                type="button"
                className="send"
                aria-label="Stop generation"
                onClick={() => abort.current?.abort()}
              >
                <Square size={14} />
              </button>
            ) : (
              <button
                className="send"
                disabled={!question.trim()}
                aria-label="Send question"
              >
                <ArrowUp size={19} />
              </button>
            )}
          </div>
        </form>
        <div className="composer-note">
          <span>
            {busy ? (
              <>
                <LoaderCircle size={10} className="spin" />
                {status}
              </>
            ) : (
              <>
                <ShieldCheck size={11} />
                Source-grounded. Always verify critical changes.
              </>
            )}
          </span>
          <kbd>↵ to send</kbd>
        </div>
      </div>
    </>
  );

  return (
    <div className={`app screen-${screen}`}>
      <header className="topbar">
        <button
          className="brand-button"
          onClick={home}
          aria-label="CodeLens home"
        >
          <Logo small={screen !== "home"} />
        </button>
        {screen === "workspace" && repo ? (
          <div className="repo-breadcrumb">
            <span>/</span>
            <Github size={14} />
            {repo.name}
            <span className="live-badge">
              <Dot /> indexed
            </span>
          </div>
        ) : (
          <nav className="top-nav">
            <a
              href="#how-it-works"
              onClick={screen !== "home" ? home : undefined}
            >
              How it works
            </a>
            <Link href="/benchmark">
              Benchmarks <span>↗</span>
            </Link>
          </nav>
        )}
        <div className="header-actions">
          {screen === "workspace" && (
            <>
              <button className="text-button hide-mobile" onClick={copyShare}>
                <ExternalLink size={14} />
                Share repo
              </button>
              <button
                className="icon-button"
                aria-label="New repository"
                onClick={home}
              >
                <Plus size={16} />
              </button>
            </>
          )}
          <button
            className="key-button"
            onClick={() => setPalette(true)}
            aria-label="Open command palette"
          >
            <Command size={13} />
            <span>K</span>
          </button>
          <button
            className="icon-button"
            aria-label="Keyboard shortcuts"
            onClick={() => setSettingsOpen(true)}
          >
            <Settings2 size={17} />
          </button>
          {screen === "home" && (
            <a
              className="github-link"
              href="https://github.com/paras151206/codelens"
              target="_blank"
              rel="noreferrer"
            >
              <Github size={16} /> GitHub <ArrowRight size={13} />
            </a>
          )}
        </div>
      </header>
      <AnimatePresence mode="wait">
        {screen === "home" ? (
          <motion.main
            key="home"
            className="landing"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
          >
            <section className="start-page">
              <div className="start-heading"><span className="section-label">YOUR CODE, EXPLAINED</span><h1>A little clarity<br />for your codebase.</h1><p>Explore a repository, ask a question, and follow the answer back to the code.</p></div>
              <section className="repository-card" aria-labelledby="open-repository">
                <div className="card-heading"><Github size={20} /><h2 id="open-repository">Open a repository</h2><span>Public GitHub repositories</span></div>
                <form className="repository-form" onSubmit={(e) => { e.preventDefault(); void ingest(); }}>
                  <label htmlFor="repository-url">Repository URL</label>
                  <div className="repository-field"><input id="repository-url" aria-label="GitHub repository URL" placeholder="https://github.com/owner/repository" value={url} onChange={(e) => setUrl(e.target.value)} required /><button className="primary" type="submit">Explore <ArrowRight size={16} /></button></div>
                </form>
                <div className="repository-example"><span>Just looking around?</span><button onClick={() => { setUrl("https://github.com/pallets/itsdangerous"); void ingest("https://github.com/pallets/itsdangerous"); }}>Try an example <ArrowRight size={13} /></button></div>
              </section>
              {recent.length > 0 && <section className="recent-list"><div className="recent-heading"><h2>Recently indexed</h2><span>{recent.length} repositories</span></div>{recent.map(r => <button key={r.id} onClick={() => loadRepo(r.id)}><Github size={17}/><span>{r.name}<small>{r.chunks.toLocaleString()} sections</small></span><ArrowRight size={16}/></button>)}</section>}
              <section id="how-it-works" className="workflow-notes"><div><span>01</span><h3>Add a repository</h3><p>We read the files and map how the project fits together.</p></div><div><span>02</span><h3>Ask about the code</h3><p>Start with the architecture or a specific function.</p></div><div><span>03</span><h3>Check the source</h3><p>Open the cited lines alongside each answer.</p></div></section>
              <footer className="simple-footer"><span>CodeLens</span><span>{connection}</span></footer>
            </section>
          </motion.main>
        ) : screen === "ingest" ? (
          <motion.main
            key="ingest"
            className="ingest-screen"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
          >
            <div className="orbit-logo">
              <Braces size={30} />
            </div>
            <div className="eyebrow">MAPPING YOUR CODEBASE</div>
            <h1>
              A little context.
              <br />A lot of possibilities.
            </h1>
            <p>
              {url || "Preparing repository"}
              <span> · local processing</span>
            </p>
            <div className="pipeline">
              {steps.map((step, i) => {
                const index = steps.indexOf(current?.stage || "Cloning"),
                  done = i < index,
                  active = i === index;
                return (
                  <div
                    key={step}
                    className={`${done ? "done" : ""} ${active ? "active" : ""}`}
                  >
                    <div className="pipeline-node">
                      {done ? (
                        <Check size={18} />
                      ) : active && !error ? (
                        <LoaderCircle size={19} className="spin" />
                      ) : (
                        <span>0{i + 1}</span>
                      )}
                    </div>
                    <span>{step}</span>
                  </div>
                );
              })}
            </div>
            <div className="progress-track">
              <motion.div
                animate={{
                  width: `${Math.min(98, (steps.indexOf(current?.stage || "Cloning") + 1) * 18)}%`,
                }}
              />
            </div>
            <div className="ingest-counters">
              <span>
                <strong>
                  {[...progress].reverse().find((e) => e.files)?.files || 0}
                </strong>
                files parsed
              </span>
              <span>
                <strong>
                  {[...progress].reverse().find((e) => e.chunks)?.chunks || 0}
                </strong>
                code chunks
              </span>
              <span>
                <strong>
                  {[...progress].reverse().find((e) => e.embedded)?.embedded ||
                    0}
                </strong>
                embeddings created
              </span>
            </div>
            <div className="ingest-log">
              <div>
                <Terminal size={13} /> LIVE ACTIVITY{" "}
                <span>{error ? "FAILED" : "PROCESSING"}</span>
              </div>
              {progress.slice(-6).map((event, i) => (
                <p key={i}>
                  <span>›</span>
                  {event.message || event.stage}
                </p>
              ))}
              {!progress.length && <p>Connecting to the indexing service…</p>}
            </div>
            {error && (
              <div className="ingest-error" role="alert">
                <p>{error}</p>
                <button className="primary" onClick={home}>
                  Back to repositories <ArrowRight size={15} />
                </button>
              </div>
            )}
            <p className="ingest-note">
              Preparing the repository for local search.
              <br />
              You can leave this page; indexing continues on the server.
            </p>
          </motion.main>
        ) : (
          <motion.main
            key="workspace"
            className={`workspace mobile-${mobilePane}`}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
          >
            <div className="mobile-tabs">
              {["files", "chat", "inspect"].map((p) => (
                <button
                  className={mobilePane === p ? "selected" : ""}
                  key={p}
                  onClick={() => setMobilePane(p)}
                >
                  {p}
                </button>
              ))}
            </div>
            <PanelGroup direction="horizontal" autoSaveId="codelens-panes">
              <Panel
                defaultSize={19}
                minSize={12}
                maxSize={32}
                className="files-pane"
              >
                {fileTree}
              </Panel>
              <PanelResizeHandle className="resize-handle" />
              <Panel defaultSize={46} minSize={28} className="chat-pane">
                {chat}
              </Panel>
              <PanelResizeHandle className="resize-handle" />
              <Panel defaultSize={35} minSize={22} className="inspect-pane">
                {inspect}
              </Panel>
            </PanelGroup>
            <div className="workspace-status">
              <span>
                <Dot /> {repo?.name}{" "}
                <span className="muted">
                  / snapshot {repo?.commit.slice(0, 7)}
                </span>
              </span>
              <span>
                {provider ||
                  "Local retrieval · provider selected on first question"}
              </span>
              <Link href="/benchmark">
                Benchmarks <ExternalLink size={10} />
              </Link>
            </div>
          </motion.main>
        )}
      </AnimatePresence>
      <AnimatePresence>
        {(palette || settingsOpen) && (
          <motion.div
            className="modal-backdrop"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            onClick={() => {
              setPalette(false);
              setSettingsOpen(false);
            }}
          >
            <motion.div
              role="dialog"
              aria-modal="true"
              aria-label={palette ? "Command palette" : "Settings"}
              className="modal"
              initial={{ scale: 0.97, y: 10 }}
              animate={{ scale: 1, y: 0 }}
              onClick={(e) => e.stopPropagation()}
              onKeyDown={(e) => {
                if (e.key === "Tab") {
                  const nodes = e.currentTarget.querySelectorAll<HTMLElement>(
                    "button, input, a[href]",
                  );
                  const first = nodes[0],
                    last = nodes[nodes.length - 1];
                  if (e.shiftKey && document.activeElement === first) {
                    e.preventDefault();
                    last.focus();
                  } else if (!e.shiftKey && document.activeElement === last) {
                    e.preventDefault();
                    first.focus();
                  }
                }
              }}
            >
              <div className="modal-title">
                <span>
                  {palette ? (
                    <>
                      <Command size={17} /> Jump to anything
                    </>
                  ) : (
                    <>
                      <Settings2 size={17} /> Keyboard shortcuts
                    </>
                  )}
                </span>
                <button
                  className="icon-button"
                  aria-label="Close dialog"
                  onClick={() => {
                    setPalette(false);
                    setSettingsOpen(false);
                  }}
                >
                  <X size={17} />
                </button>
              </div>
              {palette ? (
                <>
                  <div className="palette-input">
                    <Search size={17} />
                    <input
                      autoFocus
                      placeholder="Search files or repositories…"
                      aria-label="Search commands"
                      value={search}
                      onChange={(e) => setSearch(e.target.value)}
                    />
                    <kbd>ESC</kbd>
                  </div>
                  <button
                    className="palette-item"
                    onClick={() => {
                      home();
                      setPalette(false);
                    }}
                  >
                    <Plus size={16} />
                    Index a new repository
                  </button>
                  <Link className="palette-item" href="/benchmark">
                    <Layers3 size={16} />
                    View benchmarks
                  </Link>
                  {repo?.files
                    .filter((p) =>
                      p.toLowerCase().includes(search.toLowerCase()),
                    )
                    .slice(0, 8)
                    .map((path) => (
                      <button
                        className="palette-item"
                        key={path}
                        onClick={() => {
                          void openFile({ path, start: 1, end: 1 });
                          setPalette(false);
                        }}
                      >
                        <FileCode2 size={14} />
                        {path}
                      </button>
                    ))}
                  {recent
                    .filter((r) =>
                      r.name.toLowerCase().includes(search.toLowerCase()),
                    )
                    .slice(0, 4)
                    .map((r) => (
                      <button
                        key={r.id}
                        className="palette-item"
                        onClick={() => {
                          void loadRepo(r.id);
                          setPalette(false);
                        }}
                      >
                        <Github size={14} />
                        {r.name}
                      </button>
                    ))}
                </>
              ) : (
                <div className="settings-body">
                  <div className="shortcut-row">
                    <span>Command palette</span>
                    <kbd>Ctrl / ⌘ K</kbd>
                  </div>
                  <div className="shortcut-row">
                    <span>Focus chat</span>
                    <kbd>Ctrl / ⌘ /</kbd>
                  </div>
                </div>
              )}
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
function AtRepo() {
  return <span className="at-repo">@</span>;
}
