"use client";

import Graph from "graphology";
import forceAtlas2 from "graphology-layout-forceatlas2";
import { useEffect, useRef } from "react";
import type Sigma from "sigma";
import type { GraphSnapshot } from "@/lib/api";

type Snapshot = GraphSnapshot["graph"];
const MAX_FORCED_LABELS = 8;
type NodeAttrs = {
  x: number;
  y: number;
  size: number;
  color: string;
  label: string;
  zIndex: number;
  forceLabel: boolean;
};

/** WebGL can't read CSS variables, so resolve the theme's tokens to concrete colours. */
function palette() {
  const css = getComputedStyle(document.documentElement);
  const v = (name: string) => css.getPropertyValue(name).trim();
  return {
    critical: v("--critical"),
    riskLow: v("--risk-low"),
    riskHigh: v("--risk-high"),
    external: v("--neutral-node"),
    edge: v("--axis"),
    label: v("--ink"),
  };
}

function hexToRgb(hex: string): [number, number, number] {
  const h = hex.replace("#", "");
  return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)) as [number, number, number];
}

/** Single-hue magnitude ramp: low risk recedes toward the surface, high risk is darkest. */
function riskColor(score: number, low: string, high: string): string {
  const [a, b] = [hexToRgb(low), hexToRgb(high)];
  const mix = a.map((c, i) => Math.round(c + (b[i] - c) * score));
  return `rgb(${mix.join(",")})`;
}

export function NetworkMap({
  snapshot,
  onOpenHost,
  onHover,
}: {
  snapshot: Snapshot;
  onOpenHost: (ip: string) => void;
  onHover: (ip: string | null) => void;
}) {
  const container = useRef<HTMLDivElement>(null);
  const graphRef = useRef<Graph<NodeAttrs> | null>(null);
  const sigmaRef = useRef<Sigma<NodeAttrs> | null>(null);
  // Latest callbacks for Sigma's listeners (registered once), kept current after each render.
  const handlers = useRef({ onOpenHost, onHover });
  useEffect(() => {
    handlers.current = { onOpenHost, onHover };
  }, [onOpenHost, onHover]);

  // Create the renderer once. Sigma touches WebGL/window, so it is imported client-side only.
  useEffect(() => {
    let cancelled = false;
    const graph = new Graph<NodeAttrs>({ type: "directed", multi: false });
    graphRef.current = graph;
    import("sigma").then(({ default: SigmaCtor }) => {
      if (cancelled || !container.current) return;
      const colors = palette();
      const sigma = new SigmaCtor(graph, container.current, {
        renderEdgeLabels: false,
        labelRenderedSizeThreshold: 6,
        defaultEdgeColor: colors.edge,
        labelColor: { color: colors.label },
        labelSize: 11,
        zIndex: true,
      });
      sigma.on("enterNode", ({ node }) => {
        container.current!.style.cursor = graph.getNodeAttribute(node, "zIndex") > 0 ? "pointer" : "default";
        handlers.current.onHover(node);
      });
      sigma.on("leaveNode", () => {
        container.current!.style.cursor = "default";
        handlers.current.onHover(null);
      });
      sigma.on("clickNode", ({ node }) => {
        if (graph.getNodeAttribute(node, "zIndex") > 0) handlers.current.onOpenHost(node);
      });
      sigmaRef.current = sigma;
    });
    return () => {
      cancelled = true;
      sigmaRef.current?.kill();
      sigmaRef.current = null;
    };
  }, []);

  // Apply each new snapshot in place: keep existing positions so the map evolves, not jumps.
  useEffect(() => {
    const graph = graphRef.current;
    if (!graph) return;
    const colors = palette();
    const keep = new Set(snapshot.nodes.map((n) => n.id));
    graph.forEachNode((id) => {
      if (!keep.has(id)) graph.dropNode(id);
    });
    const fresh = graph.order === 0;
    // Force labels on only the few riskiest flagged hosts: more collide (the side table lists all).
    const named = new Set(
      snapshot.nodes
        .filter((n) => n.flagged)
        .sort((a, b) => b.score - a.score || b.degree - a.degree)
        .slice(0, MAX_FORCED_LABELS)
        .map((n) => n.id),
    );
    for (const n of snapshot.nodes) {
      const attrs: NodeAttrs = {
        x: graph.hasNode(n.id) ? graph.getNodeAttribute(n.id, "x") : Math.random() * 100,
        y: graph.hasNode(n.id) ? graph.getNodeAttribute(n.id, "y") : Math.random() * 100,
        size: (n.internal ? 4 : 2.5) + Math.min(8, Math.log2(1 + n.degree)),
        color: n.flagged
          ? colors.critical
          : n.internal
            ? riskColor(n.score, colors.riskLow, colors.riskHigh)
            : colors.external,
        // Labels only where they matter: flagged hosts and risky internal hosts.
        label: n.flagged || (n.internal && n.score > 0.5) ? n.id : "",
        zIndex: n.flagged ? 2 : n.internal ? 1 : 0,
        forceLabel: named.has(n.id),
      };
      graph.mergeNode(n.id, attrs);
    }
    graph.clearEdges();
    for (const e of snapshot.edges) {
      if (graph.hasNode(e.source) && graph.hasNode(e.target) && e.source !== e.target) {
        graph.mergeEdge(e.source, e.target, { size: 0.5 + Math.min(3, Math.log10(1 + e.flows)) });
      }
    }
    if (graph.order > 1) {
      forceAtlas2.assign(graph, {
        iterations: fresh ? 150 : 30,
        settings: { ...forceAtlas2.inferSettings(graph), barnesHutOptimize: graph.order > 200 },
      });
    }
    sigmaRef.current?.refresh();
  }, [snapshot]);

  return <div ref={container} className="h-full w-full" role="img" aria-label="Network map of the latest window" />;
}
