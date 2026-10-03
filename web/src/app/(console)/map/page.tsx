"use client";

import { useQuery } from "@tanstack/react-query";
import { AlertOctagon } from "lucide-react";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { NetworkMap } from "@/components/network-map";
import { Card, Empty, ErrorNote, RiskBar } from "@/components/ui";
import { ApiError, endpoints } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { eventTime, sensorLabel } from "@/lib/format";

function Legend() {
  const swatch = (bg: string) => <span className="size-2.5 rounded-full" style={{ background: bg }} aria-hidden />;
  return (
    <div className="flex flex-wrap items-center gap-4 text-xs text-ink-2">
      <span className="flex items-center gap-1.5">
        {swatch("var(--critical)")}
        <AlertOctagon className="size-3.5 text-critical" aria-hidden /> Above threshold this window
      </span>
      <span className="flex items-center gap-1.5">
        <span
          className="h-2.5 w-10 rounded-full"
          style={{ background: "linear-gradient(to right, var(--risk-low), var(--risk-high))" }}
          aria-hidden
        />
        Internal host, risk low → high
      </span>
      <span className="flex items-center gap-1.5">{swatch("var(--neutral-node)")} External host</span>
    </div>
  );
}

export default function MapPage() {
  const { session } = useAuth();
  const token = session!.token;
  const router = useRouter();
  const [chosen, setChosen] = useState("");
  const [hovered, setHovered] = useState<string | null>(null);

  const sensors = useQuery({ queryKey: ["sensors"], queryFn: () => endpoints.sensors(token) });
  // Default to the sensor with the most recent traffic.
  const sensor =
    chosen ||
    [...(sensors.data ?? [])].sort((a, b) => (b.last_window_start ?? 0) - (a.last_window_start ?? 0))[0]?.id ||
    "";
  const graph = useQuery({
    queryKey: ["graph", sensor],
    queryFn: () => endpoints.graph(token, sensor),
    enabled: !!sensor,
    // Keep the previous map while the *same* sensor refreshes; never show one sensor's
    // graph under another sensor's name.
    placeholderData: (previous) => (previous?.sensor_id === sensor ? previous : undefined),
    retry: (count, error) => !(error instanceof ApiError && error.status === 404) && count < 1,
  });
  const noMap = graph.error instanceof ApiError && graph.error.status === 404;

  const snap = graph.data?.graph;
  const internal = useMemo(
    () => (snap ? snap.nodes.filter((n) => n.internal).sort((a, b) => b.score - a.score) : []),
    [snap],
  );
  const hoveredNode = snap?.nodes.find((n) => n.id === hovered);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold">Live map</h1>
          <p className="text-sm text-ink-muted">
            {graph.data
              ? `Window starting ${eventTime(graph.data.window_start)} · showing ${snap!.nodes.length} of ${snap!.total_nodes.toLocaleString("en")} hosts`
              : "The latest 5-minute communication graph, redrawn every minute"}
            {graph.data && " · red = above threshold in this window (an alert needs 12 of 15)"}
          </p>
        </div>
        <label className="flex items-center gap-2 text-xs text-ink-2">
          Sensor
          <select
            value={sensor}
            onChange={(e) => setChosen(e.target.value)}
            className="rounded-md border border-line bg-surface px-2 py-1 text-sm text-ink"
          >
            {(sensors.data ?? []).map((s) => (
              <option key={s.id} value={s.id}>
                {sensorLabel(s.id)}
              </option>
            ))}
          </select>
        </label>
      </div>

      {graph.error && !noMap && <ErrorNote error={graph.error} />}
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_300px]">
        <Card className="min-w-0">
          <Legend />
          <div className="relative mt-3 h-[560px] rounded-md bg-page">
            {snap ? (
              <NetworkMap
                snapshot={snap}
                onHover={setHovered}
                onOpenHost={(ip) => router.push(`/hosts/${encodeURIComponent(sensor)}/${encodeURIComponent(ip)}`)}
              />
            ) : (
              <Empty>
                {!sensor
                  ? "No sensors yet."
                  : noMap
                    ? "No map for this sensor: it has no windows yet, or it was recorded before maps existed."
                    : "Waiting for the first window…"}
              </Empty>
            )}
            {hoveredNode && (
              <div className="pointer-events-none absolute top-3 left-3 rounded-md border border-line bg-surface px-3 py-2 text-xs shadow-sm">
                <div className="font-medium text-ink">{hoveredNode.id}</div>
                <div className="text-ink-2">
                  {hoveredNode.internal ? "internal" : "external"} · {hoveredNode.degree} connections
                </div>
                <div className="mt-1">
                  <RiskBar value={hoveredNode.score} />
                </div>
                {hoveredNode.internal && <div className="mt-1 text-ink-muted">click to open timeline</div>}
              </div>
            )}
          </div>
        </Card>
        {/* Table twin of the map: every internal host on it, reachable without the graphic. */}
        <Card title="Internal hosts on the map">
          {internal.length === 0 ? (
            <Empty>None yet.</Empty>
          ) : (
            <ul className="max-h-[560px] divide-y divide-line overflow-auto">
              {internal.map((n) => (
                <li key={n.id}>
                  <button
                    onClick={() => router.push(`/hosts/${encodeURIComponent(sensor)}/${encodeURIComponent(n.id)}`)}
                    className="flex w-full items-center justify-between gap-2 py-1.5 text-left text-sm hover:bg-surface-2"
                  >
                    <span className="flex items-center gap-1.5">
                      {n.flagged && (
                        <AlertOctagon className="size-3.5 text-critical" aria-label="above threshold this window" />
                      )}
                      {n.id}
                    </span>
                    <RiskBar value={n.score} />
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </div>
  );
}
