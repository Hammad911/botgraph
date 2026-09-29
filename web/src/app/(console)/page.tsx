"use client";

import { useQuery } from "@tanstack/react-query";
import { AlertOctagon } from "lucide-react";
import Link from "next/link";
import { AlertTrend } from "@/components/alert-trend";
import { Card, Empty, ErrorNote, RiskBar, StatTile } from "@/components/ui";
import { endpoints } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { compact, eventTime, sensorLabel } from "@/lib/format";

export default function OverviewPage() {
  const { session } = useAuth();
  const token = session!.token;
  const q = useQuery({ queryKey: ["overview"], queryFn: () => endpoints.overview(token) });

  if (q.error) return <ErrorNote error={q.error} />;
  const o = q.data;

  return (
    // Refetches keep the previous render (dimmed) instead of flashing a skeleton.
    <div className={`space-y-6 transition-opacity ${q.isFetching && o ? "opacity-80" : ""}`}>
      <header className="flex items-end justify-between">
        <div>
          <h1 className="text-xl font-semibold">Overview</h1>
          <p className="text-sm text-ink-muted">Latest window: {eventTime(o?.latest_window_start)}</p>
        </div>
      </header>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-5">
        <StatTile label="Open alerts" value={o?.open_alerts ?? "–"} tone="critical" hint="12 of 15 minutes flagged" />
        <StatTile label="Open warnings" value={o?.open_warnings ?? "–"} tone="warning" hint="3 of 5 minutes flagged" />
        <StatTile label="Hosts monitored" value={o ? compact(o.hosts_monitored) : "–"} hint="scored in the last 15 min" />
        <StatTile label="Windows scored" value={o ? compact(o.windows_scored) : "–"} hint="5-min graphs, every minute" />
        <StatTile
          label="Sensors"
          value={o ? `${o.active_sensors}/${o.sensors}` : "–"}
          hint="active / total (others learning)"
        />
      </div>

      <div className="grid gap-6 xl:grid-cols-5">
        <Card title="Warnings and alerts, last 24 hours of traffic" className="xl:col-span-3">
          {o ? <AlertTrend data={o.trend} /> : <Empty>Loading…</Empty>}
        </Card>
        <Card title="Riskiest hosts right now" className="xl:col-span-2">
          {!o || o.top_hosts.length === 0 ? (
            <Empty>No hosts scored yet. Start a replay with `botgraph run`.</Empty>
          ) : (
            <table className="w-full text-sm">
              <thead className="text-left text-xs text-ink-muted">
                <tr>
                  <th className="pb-2 font-normal">Host</th>
                  <th className="pb-2 font-normal">Sensor</th>
                  <th className="pb-2 font-normal">Peak score (15 min)</th>
                </tr>
              </thead>
              <tbody>
                {o.top_hosts.map((h) => (
                  <tr key={`${h.sensor_id}/${h.ip}`} className="border-t border-line">
                    <td className="py-2">
                      <Link
                        href={`/hosts/${encodeURIComponent(h.sensor_id)}/${encodeURIComponent(h.ip)}`}
                        className="inline-flex items-center gap-1.5 font-medium hover:underline"
                      >
                        {h.open_alert && (
                          <AlertOctagon className="size-3.5 text-critical" aria-label="open alert" />
                        )}
                        {h.ip}
                      </Link>
                    </td>
                    <td className="max-w-40 truncate py-2 pl-2 text-xs text-ink-2" title={h.sensor_id}>
                      {sensorLabel(h.sensor_id)}
                    </td>
                    <td className="py-2">
                      <RiskBar value={h.peak_score} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      </div>
    </div>
  );
}
