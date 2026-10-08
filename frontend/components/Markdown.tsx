"use client";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import { toast } from "sonner";
import type { Citation } from "@/lib/api";
export default function Markdown({
  text,
  open,
}: {
  text: string;
  open: (c: Citation) => void;
}) {
  const linked = text.replace(
    /\[([^\[\]\n]+):(\d+)\s*[-\u2010-\u2015\u2212]\s*(\d+)\]/g,
    (_, path, start, end) =>
      `[${path}:${start}–${end}](#source=${encodeURIComponent(path)}&start=${start}&end=${end})`,
  );
  return (
    <div className="markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeHighlight]}
        components={{
          a: ({ href, children }) =>
            href?.startsWith("#source=") ? (
              <button
                className="citation"
                onClick={() => {
                  const p = new URLSearchParams(href.slice(1));
                  open({
                    path: p.get("source")!,
                    start: Number(p.get("start")),
                    end: Number(p.get("end")),
                  });
                }}
              >
                {children}
              </button>
            ) : (
              <a href={href} target="_blank" rel="noopener noreferrer">
                {children}
              </a>
            ),
          pre: ({ children }) => (
            <div className="code-block">
              <button
                aria-label="Copy code"
                onClick={async (e) => {
                  await navigator.clipboard.writeText(
                    e.currentTarget.parentElement?.querySelector("pre")
                      ?.textContent || "",
                  );
                  toast.success("Code copied");
                }}
              >
                Copy
              </button>
              <pre>{children}</pre>
            </div>
          ),
        }}
      >
        {linked}
      </ReactMarkdown>
    </div>
  );
}
