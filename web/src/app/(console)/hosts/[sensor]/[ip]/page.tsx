"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, BarChart3, Table2 } from "lucide-react";
import Link from "next/link";
import { use, useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Card, Empty, ErrorNote, LevelBadge, StatusPill } from "@/components/ui";
import { endpoints } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { eventTime, hourLabel, sensorLabel } from "@/lib/format";

type Point = { window_start: number; score: number };

function Tip({ active, payload }: { active?: boolean; payload?: { payload: Point }[] }) {
  if (!active || !payload?.length) return null;
  const p = payload[0].payload;
  return (
    <div className="rounded-md border border-line bg-surface px-3 py-2 text-xs shadow-sm">
      <div className="font-medium text-ink">{eventTime(p.window_start)}</div>
      <div className="tabular text-ink-2">Score {p.score.toFixed(4)}</div>
    </div>
  );
}

export default function HostPage({ params }: { params: Promise<{ sensor: string; ip: string }> }) {
  const { sensor: rawSensor, ip: rawIp } = use(params);
  const sensor = decodeURIComponent(rawSensor);
  const ip = decodeURIComponent(rawIp);
  const { session } = useAuth();
  const [table, setTable] = useState(false);
  const q = useQuery({
    queryKey: ["host", sensor, ip],
    queryFn: () => endpoints.host(session!.token, sensor, ip),
  });

  if (q.error) return <ErrorNote error={q.error} />;
  const h = q.data;
  if (!h) return <Empty>Loading…</Empty>;
  const alertLevels = new Map(h.alerts.map((a) => [a.window_start, a.level]));

  return (
    <div className="space-y-6">
      <Link href="/alerts" className="inline-flex items-center gap-1 text-sm text-ink-2 hover:text-ink">
        <ArrowLeft className="size-4" aria-hidden /> Alerts
      </Link>
      <header>
        <h1 className="text-xl font-semibold">{ip}</h1>
        <p className="text-sm text-ink-muted">
          {sensorLabel(sensor)} · {h.timeline.length} one-minute windows
          {h.threshold != null && <> · alert threshold {h.threshold.toFixed(4)}</>}
        </p>
      </header>

      <Card
        title="Risk score over time"
        action={
          <button
            onClick={() => setTable((t) => !t)}
            className="flex items-center gap-1 text-xs text-ink-2 hover:text-ink"
            aria-pressed={table}
          >
            {table ? <BarChart3 className="size-3.5" aria-hidden /> : <Table2 className="size-3.5" aria-hidden />}
            {table ? "Chart" : "Table"}
          </button>
        }
      >
        {h.timeline.length === 0 ? (
          <Empty>No scores kept for this host (outside the retention window).</Empty>
        ) : table ? (
          <div className="max-h-80 overflow-auto">
            <table className="w-full text-xs">
              <thead className="text-left text-ink-muted">
                <tr>
                  <th className="py-1 font-normal">Window (UTC, traffic time)</th>
                  <th className="py-1 text-right font-normal">Score</th>
                  <th className="py-1 font-normal">Event</th>
                </tr>
              </thead>
              <tbody className="tabular">
                {h.timeline.map((p) => (
                  <tr key={p.window_start} className="border-t border-line">
                    <td className="py-1">{eventTime(p.window_start)}</td>
                    <td className="py-1 text-right">{p.score.toFixed(4)}</td>
                    <td className="py-1">
                      {alertLevels.has(p.window_start) && <LevelBadge level={alertLevels.get(p.window_start)!} />}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="h-72">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart data={h.timeline} margin={{ top: 16, right: 16, bottom: 0, left: -12 }}>
                <CartesianGrid vertical={false} stroke="var(--grid)" />
                <XAxis
                  dataKey="window_start"
                  type="number"
                  domain={["dataMin", "dataMax"]}
                  tickFormatter={hourLabel}
                  tick={{ fill: "var(--ink-muted)", fontSize: 11 }}
                  axisLine={{ stroke: "var(--axis)" }}
                  tickLine={false}
                />
                <YAxis
                  domain={[0, 1]}
                  tick={{ fill: "var(--ink-muted)", fontSize: 11 }}
                  axisLine={false}
                  tickLine={false}
                />
                <Tooltip content={<Tip />} cursor={{ stroke: "var(--axis)" }} />
                {h.threshold != null && (
                  <ReferenceLine
                    y={h.threshold}
                    stroke="var(--ink-2)"
                    label={{ value: "threshold", position: "insideTopRight", fill: "var(--ink-2)", fontSize: 11 }}
                  />
                )}
                {h.alerts.map((a) => (
                  <ReferenceLine
                    key={a.id}
                    x={a.window_start}
                    stroke={a.level === "alert" ? "var(--critical)" : "var(--warning)"}
                    label={{
                      value: a.level === "alert" ? "alert" : "warning",
                      position: "top",
                      fill: "var(--ink-2)",
                      fontSize: 10,
                    }}
                  />
                ))}
                <Line
                  dataKey="score"
                  stroke="var(--accent)"
                  strokeWidth={2}
                  dot={false}
                  activeDot={{ r: 4, stroke: "var(--surface)", strokeWidth: 2 }}
                  isAnimationActive={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
      </Card>

      <Card title="Alerts for this host">
        {h.alerts.length === 0 ? (
          <Empty>None.</Empty>
        ) : (
          <ul className="divide-y divide-line">
            {h.alerts.map((a) => (
              <li key={a.id} className="flex items-center gap-3 py-2 text-sm">
                <LevelBadge level={a.level} />
                <Link href={`/alerts/${a.id}`} className="tabular hover:underline">
                  {eventTime(a.window_start)}
                </Link>
                <StatusPill status={a.status} />
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}
