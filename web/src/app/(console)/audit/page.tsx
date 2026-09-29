"use client";

import { useQuery } from "@tanstack/react-query";
import { Card, Empty, ErrorNote } from "@/components/ui";
import { endpoints, type AuditEvent } from "@/lib/api";
import { useAuth } from "@/lib/auth";

const ACTIONS: Record<string, string> = {
  login: "Signed in",
  login_failed: "Failed sign-in",
  logout: "Signed out everywhere",
  alert_triaged: "Triaged alert",
  sensor_recalibrated: "Recalibrated sensor",
};

function detail(e: AuditEvent): string {
  if (!e.detail) return "";
  return Object.entries(e.detail)
    .map(([k, v]) => `${k}: ${typeof v === "string" ? v : JSON.stringify(v)}`)
    .join(" · ");
}

export default function AuditPage() {
  const { session, can } = useAuth();
  const token = session!.token;
  const q = useQuery({
    queryKey: ["audit"],
    queryFn: () => endpoints.audit(token),
    enabled: can("admin"),
  });

  if (!can("admin")) return <Empty>The audit log is visible to admins only.</Empty>;

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Audit log</h1>
      <p className="max-w-2xl text-sm text-ink-2">
        Who signed in and who changed what: triage, recalibration and failed sign-ins (the reason, such as a
        locked account, is recorded here and never shown on the login page).
      </p>
      {q.error && <ErrorNote error={q.error} />}
      <Card>
        {!q.data ? (
          <Empty>Loading…</Empty>
        ) : q.data.length === 0 ? (
          <Empty>Nothing recorded yet.</Empty>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-left text-xs text-ink-muted">
              <tr>
                <th className="pb-2 font-normal">When (UTC)</th>
                <th className="pb-2 font-normal">Who</th>
                <th className="pb-2 font-normal">Action</th>
                <th className="pb-2 font-normal">Target</th>
                <th className="pb-2 font-normal">Details</th>
                <th className="pb-2 font-normal">From</th>
              </tr>
            </thead>
            <tbody>
              {q.data.map((e) => (
                <tr key={e.id} className="border-t border-line align-top">
                  <td className="tabular py-2 text-xs text-ink-2">{e.at.replace("T", " ").slice(0, 19)}</td>
                  <td className="py-2">{e.actor ?? "–"}</td>
                  <td className="py-2">{ACTIONS[e.action] ?? e.action}</td>
                  <td className="py-2 text-ink-2">{e.target ?? ""}</td>
                  <td className="py-2 text-xs text-ink-2">{detail(e)}</td>
                  <td className="tabular py-2 text-xs text-ink-muted">{e.client ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
