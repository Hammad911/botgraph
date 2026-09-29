"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, ArrowRight } from "lucide-react";
import Link from "next/link";
import { use, useState } from "react";
import { Button, Card, Empty, ErrorNote, LevelBadge, RiskBar, STATUS_OPTIONS, StatusPill } from "@/components/ui";
import { endpoints, type AlertDetail, type AlertStatus } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { EDGE_FEATURES, NODE_FEATURES, describe } from "@/lib/features";
import { eventTime, sensorLabel } from "@/lib/format";

function Explanation({ alert }: { alert: AlertDetail }) {
  const e = alert.explanation;
  if (!e) {
    return <Empty>No explanation stored (warning-level events and replays with explanations off).</Empty>;
  }
  if (e.top_features.length === 0 && e.top_flows.length === 0) {
    // Recorded by the earlier GNNExplainer version, which produced empty masks on saturated scores.
    return <Empty>This alert&apos;s stored explanation is empty (recorded by an older explainer).</Empty>;
  }
  const maxFeature = Math.max(...e.top_features.map((f) => f.importance), 1e-9);
  return (
    <div className="space-y-5">
      <div>
        <h3 className="mb-2 text-xs font-medium tracking-wide text-ink-muted uppercase">
          What about this host pushed the score up
        </h3>
        <ul className="space-y-1.5">
          {e.top_features.map((f) => {
            const d = describe(NODE_FEATURES, f.feature, f.value);
            return (
              <li key={f.feature} className="flex items-center gap-3 text-sm">
                <span
                  className="h-1.5 shrink-0 rounded-full"
                  style={{ width: `${(f.importance / maxFeature) * 80}px`, background: "var(--risk-mid)" }}
                  aria-hidden
                />
                <span className="flex-1">{d.label}</span>
                <span className="tabular text-ink-2">{d.value}</span>
              </li>
            );
          })}
        </ul>
      </div>
      <p className="text-xs text-ink-muted">
        Integrated Gradients: each bar is how much that property raised this host&apos;s score compared
        with an average host.
      </p>
      <div>
        <h3 className="mb-2 text-xs font-medium tracking-wide text-ink-muted uppercase">
          The connections that mattered most
        </h3>
        {e.top_flows.length === 0 ? (
          <p className="text-sm text-ink-muted">No single flow stood out.</p>
        ) : (
          <ul className="space-y-2">
            {e.top_flows.map((flow) => {
              // Timing is only meaningful with 2+ connections on the flow.
              const single = Math.expm1(flow.features.log_flows ?? 0) < 1.5;
              const parts = ["log_flows", "periodicity", "log_iat_mean", "log_dst_ports", "failed_ratio"]
                .filter((k) => k in flow.features && !(single && (k === "periodicity" || k === "log_iat_mean")))
                .map((k) => describe(EDGE_FEATURES, k, flow.features[k]))
                .map((d) => `${d.label}: ${d.value}`);
              return (
                <li key={`${flow.src}-${flow.dst}`} className="rounded-md border border-line px-3 py-2 text-sm">
                  <div className="flex items-center gap-1.5 font-medium">
                    {flow.src} <ArrowRight className="size-3.5 text-ink-muted" aria-hidden /> {flow.dst}
                  </div>
                  <div className="mt-0.5 text-xs text-ink-2">{parts.join(" · ")}</div>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}

function Triage({ alert }: { alert: AlertDetail }) {
  const { session, can } = useAuth();
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<AlertStatus>(alert.status);
  const [assignee, setAssignee] = useState(alert.assignee ?? "");
  const [note, setNote] = useState(alert.note ?? "");
  const save = useMutation({
    mutationFn: () => endpoints.triage(session!.token, alert.id, { status, assignee, note }),
    onSuccess: (updated) => {
      queryClient.setQueryData(["alert", alert.id], updated);
      queryClient.invalidateQueries({ queryKey: ["alerts"] });
      queryClient.invalidateQueries({ queryKey: ["overview"] });
    },
  });
  const editable = can("analyst");

  return (
    <form
      className="space-y-3"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <label className="block text-sm">
        <span className="text-xs text-ink-2">Status</span>
        <select
          disabled={!editable}
          value={status}
          onChange={(e) => setStatus(e.target.value as AlertStatus)}
          className="mt-1 w-full rounded-md border border-line bg-page px-2 py-1.5"
        >
          {STATUS_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </label>
      <label className="block text-sm">
        <span className="text-xs text-ink-2">Assignee</span>
        <input
          disabled={!editable}
          value={assignee}
          onChange={(e) => setAssignee(e.target.value)}
          className="mt-1 w-full rounded-md border border-line bg-page px-2 py-1.5"
          placeholder={session?.username}
        />
      </label>
      <label className="block text-sm">
        <span className="text-xs text-ink-2">Note</span>
        <textarea
          disabled={!editable}
          value={note}
          onChange={(e) => setNote(e.target.value)}
          rows={4}
          maxLength={2000}
          className="mt-1 w-full rounded-md border border-line bg-page px-2 py-1.5"
        />
      </label>
      {save.error && <ErrorNote error={save.error} />}
      {editable ? (
        <Button type="submit" disabled={save.isPending}>
          {save.isPending ? "Saving…" : save.isSuccess ? "Saved" : "Save"}
        </Button>
      ) : (
        <p className="text-xs text-ink-muted">Viewers can read alerts; analysts can triage them.</p>
      )}
    </form>
  );
}

export default function AlertPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const alertId = Number(id);
  const { session } = useAuth();
  const q = useQuery({
    queryKey: ["alert", alertId],
    queryFn: () => endpoints.alert(session!.token, alertId),
    enabled: Number.isFinite(alertId),
  });

  if (q.error) return <ErrorNote error={q.error} />;
  const a = q.data;
  if (!a) return <Empty>Loading…</Empty>;

  return (
    <div className="space-y-6">
      <Link href="/alerts" className="inline-flex items-center gap-1 text-sm text-ink-2 hover:text-ink">
        <ArrowLeft className="size-4" aria-hidden /> Alerts
      </Link>
      <header className="flex flex-wrap items-center gap-3">
        <LevelBadge level={a.level} />
        <h1 className="text-xl font-semibold">{a.ip}</h1>
        <StatusPill status={a.status} />
        <Link
          href={`/hosts/${encodeURIComponent(a.sensor_id)}/${encodeURIComponent(a.ip)}`}
          className="ml-auto text-sm text-accent hover:underline"
        >
          Host timeline →
        </Link>
      </header>

      <div className="grid gap-6 lg:grid-cols-3">
        <Card title="Why BotGraph flagged this host" className="lg:col-span-2">
          <p className="mb-4 text-sm text-ink-2">
            Scored <RiskBar value={a.score} threshold={a.threshold} /> against a threshold of{" "}
            <span className="tabular">{a.threshold.toFixed(3)}</span>, and flagged in {a.hits} of its last{" "}
            {a.rule_n} one-minute windows on <b>{sensorLabel(a.sensor_id)}</b> at {eventTime(a.window_start)}.
            {a.cleared_window_start != null && <> Cleared at {eventTime(a.cleared_window_start)}.</>}
          </p>
          <Explanation alert={a} />
        </Card>
        <Card title="Triage">
          <Triage alert={a} />
        </Card>
      </div>
    </div>
  );
}
