"use client";
import Editor, { loader, type OnMount } from "@monaco-editor/react";
import { useEffect, useRef } from "react";
import type { editor } from "monaco-editor";
import type { Citation } from "@/lib/api";
loader.config({ paths: { vs: "/monaco/vs" } });
export default function CodeViewer({
  file,
  citation,
}: {
  file: { path: string; text: string };
  citation?: Citation;
}) {
  const ref = useRef<editor.IStandaloneCodeEditor | null>(null);
  const decorations = useRef<editor.IEditorDecorationsCollection | null>(null);
  function highlight() {
    if (!ref.current) return;
    decorations.current?.clear();
    if (citation) {
      decorations.current = ref.current.createDecorationsCollection([
        {
          range: {
            startLineNumber: citation.start,
            startColumn: 1,
            endLineNumber: citation.end,
            endColumn: 1,
          },
          options: {
            isWholeLine: true,
            className: "source-highlight",
            glyphMarginClassName: "source-glyph",
          },
        },
      ]);
      ref.current.revealLineInCenter(citation.start, 0);
    }
  }
  useEffect(highlight, [citation, file]);
  const mount: OnMount = (ed) => {
    ref.current = ed;
    highlight();
  };
  const language =
    (
      {
        py: "python",
        ts: "typescript",
        tsx: "typescript",
        js: "javascript",
        jsx: "javascript",
        rs: "rust",
        go: "go",
        java: "java",
        cpp: "cpp",
        c: "c",
        json: "json",
        md: "markdown",
        yml: "yaml",
      } as Record<string, string>
    )[file.path.split(".").pop()!] || "plaintext";
  return (
    <Editor
      path={file.path}
      value={file.text}
      language={language}
      theme="vs-dark"
      onMount={mount}
      loading={<div className="skeleton">Loading editor…</div>}
      options={{
        readOnly: true,
        fontSize: 12,
        fontFamily: "Consolas, monospace",
        minimap: { enabled: false },
        padding: { top: 20 },
        scrollBeyondLastLine: false,
        automaticLayout: true,
        glyphMargin: true,
        wordWrap: "on",
      }}
    />
  );
}
