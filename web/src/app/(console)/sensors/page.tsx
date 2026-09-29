"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, GraduationCap } from "lucide-react";
import { Button, Card, Empty, ErrorNote } from "@/components/ui";
import { endpoints } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { eventTime, sensorLabel } from "@/lib/format";

export default function SensorsPage() {
  const { session, can } = useAuth();
  const token = session!.token;
  const queryClient = useQueryClient();
  const q = useQuery({ queryKey: ["sensors"], queryFn: () => endpoints.sensors(token) });
  const recalibrate = useMutation({
    mutationFn: (id: string) => endpoints.recalibrate(token, id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["sensors"] }),
  });

  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">Sensors</h1>
      <p className="max-w-2xl text-sm text-ink-2">
        Each monitored network starts in <b>learning</b> mode: BotGraph watches its normal traffic and then sets
        the alert threshold to the 95th percentile of those scores (never below the model&apos;s own threshold).
      </p>
      {q.error && <ErrorNote error={q.error} />}
      {recalibrate.error && <ErrorNote error={recalibrate.error} />}
      <Card>
        {!q.data ? (
          <Empty>Loading…</Empty>
        ) : q.data.length === 0 ? (
          <Empty>No sensors yet. Start one with `botgraph run --replay …`.</Empty>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-left text-xs text-ink-muted">
              <tr>
                <th className="pb-2 font-normal">Sensor</th>
                <th className="pb-2 font-normal">Mode</th>
                <th className="pb-2 text-right font-normal">Threshold</th>
                <th className="pb-2 text-right font-normal">Model threshold</th>
                <th className="pb-2 font-normal">Last window</th>
                <th className="pb-2" />
              </tr>
            </thead>
            <tbody>
              {q.data.map((s) => (
                <tr key={s.id} className="border-t border-line">
                  <td className="py-2 font-medium">{sensorLabel(s.id)}</td>
                  <td className="py-2">
                    {s.state === "active" ? (
                      <span className="inline-flex items-center gap-1 text-xs">
                        <CheckCircle2 className="size-3.5 text-good" aria-hidden /> Active
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1 text-xs text-ink-2">
                        <GraduationCap className="size-3.5" aria-hidden /> Learning
                      </span>
                    )}
                  </td>
                  <td className="tabular py-2 text-right">{s.threshold?.toFixed(4) ?? "–"}</td>
                  <td className="tabular py-2 text-right text-ink-2">{s.model_threshold?.toFixed(4) ?? "–"}</td>
                  <td className="tabular py-2 text-xs text-ink-2">{eventTime(s.last_window_start)}</td>
                  <td className="py-2 text-right">
                    {can("admin") && s.state === "active" && (
                      <Button
                        variant="ghost"
                        disabled={recalibrate.isPending}
                        onClick={() => recalibrate.mutate(s.id)}
                        title="Back to learning mode on the next detector start"
                      >
                        Recalibrate
                      </Button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
