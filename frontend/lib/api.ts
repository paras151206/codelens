// Requests go through the same-origin server. Credentials never enter the browser.
export const API = "/api/gateway";
export function headers(): Record<string, string> {
  return { "Content-Type": "application/json" };
}
let sessionReady: Promise<void> | undefined;
function ensureSession() {
  if (!sessionReady) sessionReady = fetch(API + "/api/health", { cache: "no-store", signal: AbortSignal.timeout(90_000) })
    .then(response => { if (!response.ok) throw new Error("The service is waking up. Please retry shortly."); })
    .catch(error => { sessionReady = undefined; throw error; });
  return sessionReady;
}
export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  await ensureSession();
  const response = await fetch(API + path, {
    ...options,
    signal: options?.signal ?? AbortSignal.timeout(90_000),
    headers: { ...headers(), ...options?.headers },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(
      typeof body.detail === "string"
        ? body.detail
        : `Request failed (${response.status})`,
    );
  }
  return response.json();
}
export async function stream(
  path: string,
  onEvent: (event: StreamEvent) => void,
  body?: unknown,
  signal?: AbortSignal,
) {
  await ensureSession();
  const response = await fetch(API + path, {
    method: body ? "POST" : "GET",
    headers: headers(),
    ...(body ? { body: JSON.stringify(body) } : {}),
    signal,
  });
  if (!response.ok) {
    const result = await response.json().catch(() => ({}));
    throw new Error(result.detail || `Request failed (${response.status})`);
  }
  if (!response.body) throw new Error("Streaming response unavailable");
  const reader = response.body.getReader(),
    decoder = new TextDecoder();
  let buffer = "",
    finished = false;
  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += decoder.decode(value, { stream: !done }).replace(/\r\n/g, "\n");
      let split: number;
      while ((split = buffer.indexOf("\n\n")) >= 0) {
        const frame = buffer.slice(0, split);
        buffer = buffer.slice(split + 2);
        const data = frame
          .split("\n")
          .filter((x) => x.startsWith("data:"))
          .map((x) => x.slice(5).trim())
          .join("\n");
        if (data) {
          const event = JSON.parse(data) as StreamEvent;
          onEvent(event);
          if (event.type === "error") throw new Error(event.message);
          if (event.type === "done") finished = true;
        }
      }
      if (done) break;
    }
    if (!finished)
      throw new Error("Connection ended before completion. Please retry.");
  } finally {
    reader.releaseLock();
  }
}
export type Citation = { path: string; start: number; end: number };
export type Score = Citation & {
  id: string;
  dense: number;
  bm25: number;
  rrf: number;
  rerank: number | null;
  chosen: boolean;
  reason: string;
};
export type Retrieval = {
  queries: string[];
  intent: string;
  scores: Score[];
  warning?: string;
  chunks: { path: string }[];
};
export type Repo = {
  id: string;
  name: string;
  url: string;
  files: string[];
  chunks: number;
  commit: string;
  graph: {
    nodes: { id: string }[];
    links: { source: string; target: string }[];
  };
};
export type StreamEvent = {
  type: string;
  text?: string;
  message?: string;
  stage?: string;
  files?: number;
  chunks?: number;
  embedded?: number;
  repo?: Repo;
  answer?: string;
  citations?: Citation[];
  warnings?: string[];
  suggestions?: string[];
  conversation?: string;
  reason?: string;
  provider?: string;
  model?: string;
} & Partial<Omit<Retrieval, "chunks">>;
