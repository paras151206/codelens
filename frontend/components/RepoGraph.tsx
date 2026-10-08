"use client";
import dynamic from "next/dynamic";
import { useEffect, useRef, useState } from "react";
import type { Repo } from "@/lib/api";
const ForceGraph = dynamic(() => import("react-force-graph-2d"), {
  ssr: false,
});
export default function RepoGraph({
  repo,
  cited,
  open,
}: {
  repo: Repo;
  cited: string[];
  open: (path: string) => void;
}) {
  const box = useRef<HTMLDivElement>(null),
    [size, setSize] = useState({ width: 600, height: 450 });
  const [graph] = useState(() => JSON.parse(JSON.stringify(repo.graph)));
  useEffect(() => {
    const observer = new ResizeObserver((entries) => {
      const r = entries[0].contentRect;
      setSize({ width: r.width, height: Math.max(r.height, 300) });
    });
    if (box.current) observer.observe(box.current);
    return () => observer.disconnect();
  }, []);
  return (
    <div ref={box} className="graph">
      <ForceGraph
        {...size}
        graphData={graph}
        backgroundColor="#0a0e15"
        nodeLabel="id"
        linkColor={() => "#29384d"}
        nodeColor={(n) =>
          cited.includes(String(n.id)) ? "#c1fa72" : "#7180f8"
        }
        nodeRelSize={5}
        onNodeClick={(n) => open(String(n.id))}
        nodeCanvasObjectMode={() => "after"}
        nodeCanvasObject={(n, ctx, scale) => {
          if (!cited.includes(String(n.id))) return;
          ctx.fillStyle = "#c1fa72";
          ctx.font = `${12 / scale}px monospace`;
          ctx.fillText(String(n.id).split("/").pop()!, n.x! + 8, n.y! + 3);
        }}
      />
    </div>
  );
}
