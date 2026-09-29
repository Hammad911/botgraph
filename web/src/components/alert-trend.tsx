"use client";

import { AlertOctagon, AlertTriangle, Table2, BarChart3 } from "lucide-react";
import { useState } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { Overview } from "@/lib/api";
import { eventTime, hourLabel } from "@/lib/format";

type Point = Overview["trend"][number];

function Legend() {
  // Two series: a legend is always present, with icon + label so identity is never colour alone.
  return (
    <div className="flex items-center gap-4 text-xs text-ink-2">
      <span className="flex items-center gap-1.5">
        <span className="size-2.5 rounded-sm" style={{ background: "var(--warning)" }} aria-hidden />
        <AlertTriangle className="size-3.5" aria-hidden /> Warnings
      </span>
      <span className="flex items-center gap-1.5">
        <span className="size-2.5 rounded-sm" style={{ background: "var(--critical)" }} aria-hidden />
        <AlertOctagon className="size-3.5" aria-hidden /> Alerts
      </span>
    </div>
  );
}

function Tip({ active, payload }: { active?: boolean; payload?: { payload: Point }[] }) {
  if (!active || !payload?.length) return null;
  const p = payload[0].payload;
  return (
    <div className="rounded-md border border-line bg-surface px-3 py-2 text-xs shadow-sm">
      <div className="mb-1 font-medium text-ink">{eventTime(p.hour_start)}</div>
      <div className="tabular text-ink-2">Warnings: {p.warnings}</div>
      <div className="tabular text-ink-2">Alerts: {p.alerts}</div>
    </div>
  );
}

export function AlertTrend({ data }: { data: Point[] }) {
  const [table, setTable] = useState(false);
  const total = data.reduce((s, p) => s + p.warnings + p.alerts, 0);

  return (
    <div>
      <div className="mb-3 flex items-center justify-between">
        <Legend />
        <button
          onClick={() => setTable((t) => !t)}
          className="flex items-center gap-1 text-xs text-ink-2 hover:text-ink"
          aria-pressed={table}
        >
          {table ? <BarChart3 className="size-3.5" aria-hidden /> : <Table2 className="size-3.5" aria-hidden />}
          {table ? "Chart" : "Table"}
        </button>
      </div>
      {table ? (
        <div className="max-h-64 overflow-auto">
          <table className="w-full text-xs">
            <thead className="text-left text-ink-muted">
              <tr>
                <th className="py-1 font-normal">Hour (UTC, event time)</th>
                <th className="py-1 text-right font-normal">Warnings</th>
                <th className="py-1 text-right font-normal">Alerts</th>
              </tr>
            </thead>
            <tbody className="tabular">
              {data.map((p) => (
                <tr key={p.hour_start} className="border-t border-line">
                  <td className="py-1">{eventTime(p.hour_start)}</td>
                  <td className="py-1 text-right">{p.warnings}</td>
                  <td className="py-1 text-right">{p.alerts}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : total === 0 ? (
        <p className="py-16 text-center text-sm text-ink-muted">No warnings or alerts in the last 24 hours.</p>
      ) : (
        // Height includes the x-axis band, so the card never gets a nested scroll.
        <div className="h-60">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={data} margin={{ top: 4, right: 4, bottom: 0, left: -20 }}>
              <CartesianGrid vertical={false} stroke="var(--grid)" />
              <XAxis
                dataKey="hour_start"
                tickFormatter={hourLabel}
                tick={{ fill: "var(--ink-muted)", fontSize: 11 }}
                axisLine={{ stroke: "var(--axis)" }}
                tickLine={false}
                interval={3}
              />
              <YAxis
                allowDecimals={false}
                tick={{ fill: "var(--ink-muted)", fontSize: 11 }}
                axisLine={false}
                tickLine={false}
              />
              <Tooltip content={<Tip />} cursor={{ fill: "var(--surface-2)" }} />
              {/* 2px surface-coloured stroke = the gap between stacked segments, not a border */}
              <Bar dataKey="warnings" stackId="a" fill="var(--warning)" stroke="var(--surface)" strokeWidth={2} />
              <Bar
                dataKey="alerts"
                stackId="a"
                fill="var(--critical)"
                stroke="var(--surface)"
                strokeWidth={2}
                radius={[4, 4, 0, 0]}
              />
            </BarChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}
