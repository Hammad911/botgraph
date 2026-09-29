"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { Card, Empty, ErrorNote, LevelBadge, RiskBar, STATUS_OPTIONS, StatusPill } from "@/components/ui";
import { endpoints } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { eventTime, sensorLabel } from "@/lib/format";

function Select({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
}) {
  return (
    <label className="flex items-center gap-2 text-xs text-ink-2">
      {label}
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="rounded-md border border-line bg-surface px-2 py-1 text-sm text-ink"
      >
        <option value="">All</option>
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  );
}

export default function AlertsPage() {
  const { session } = useAuth();
  const token = session!.token;
  const [status, setStatus] = useState("open");
  const [level, setLevel] = useState("");
  const [sensor, setSensor] = useState("");

  const sensors = useQuery({ queryKey: ["sensors"], queryFn: () => endpoints.sensors(token) });
  const params: Record<string, string> = { limit: "500" };
  if (status) params.status = status;
  if (level) params.level = level;
  if (sensor) params.sensor = sensor;
  const q = useQuery({ queryKey: ["alerts", params], queryFn: () => endpoints.alerts(token, params) });

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Alerts</h1>
      {/* One filter row above everything it scopes. */}
      <div className="flex flex-wrap items-center gap-4">
        <Select label="Status" value={status} onChange={setStatus} options={STATUS_OPTIONS} />
        <Select
          label="Level"
          value={level}
          onChange={setLevel}
          options={[
            { value: "alert", label: "Alert" },
            { value: "warning", label: "Warning" },
          ]}
        />
        <Select
          label="Sensor"
          value={sensor}
          onChange={setSensor}
          options={(sensors.data ?? []).map((s) => ({ value: s.id, label: sensorLabel(s.id) }))}
        />
        <span className="ml-auto text-xs text-ink-muted">{q.data ? `${q.data.length} shown` : ""}</span>
      </div>

      {q.error ? (
        <ErrorNote error={q.error} />
      ) : (
        <Card>
          {!q.data ? (
            <Empty>Loading…</Empty>
          ) : q.data.length === 0 ? (
            <Empty>No alerts match these filters.</Empty>
          ) : (
            <div className={`overflow-x-auto transition-opacity ${q.isFetching ? "opacity-80" : ""}`}>
              <table className="w-full text-sm">
                <thead className="text-left text-xs text-ink-muted">
                  <tr>
                    <th className="pb-2 font-normal">Level</th>
                    <th className="pb-2 font-normal">Host</th>
                    <th className="pb-2 font-normal">Sensor</th>
                    <th className="pb-2 font-normal">Raised (traffic time)</th>
                    <th className="pb-2 font-normal">Score</th>
                    <th className="pb-2 font-normal">Status</th>
                    <th className="pb-2 font-normal">Assignee</th>
                  </tr>
                </thead>
                <tbody>
                  {q.data.map((a) => (
                    <tr key={a.id} className="border-t border-line hover:bg-surface-2">
                      <td className="py-2">
                        <LevelBadge level={a.level} />
                      </td>
                      <td className="py-2">
                        <Link href={`/alerts/${a.id}`} className="font-medium hover:underline">
                          {a.ip}
                        </Link>
                      </td>
                      <td className="py-2 text-xs text-ink-2">{sensorLabel(a.sensor_id)}</td>
                      <td className="tabular py-2 text-xs text-ink-2">{eventTime(a.window_start)}</td>
                      <td className="py-2">
                        <RiskBar value={a.score} threshold={a.threshold} />
                      </td>
                      <td className="py-2">
                        <StatusPill status={a.status} />
                      </td>
                      <td className="py-2 text-xs text-ink-2">{a.assignee ?? "–"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      )}
    </div>
  );
}
