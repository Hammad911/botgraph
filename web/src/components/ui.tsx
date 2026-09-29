import { AlertOctagon, AlertTriangle, CheckCircle2, CircleDot, Search, XCircle } from "lucide-react";
import type { AlertStatus } from "@/lib/api";

export function Card({
  title,
  action,
  children,
  className = "",
}: {
  title?: string;
  action?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section className={`rounded-lg border border-line bg-surface ${className}`}>
      {title && (
        <header className="flex items-center justify-between border-b border-line px-4 py-3">
          <h2 className="text-sm font-semibold text-ink">{title}</h2>
          {action}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

/** A headline number. Proportional figures on purpose (no tabular-nums at display size). */
export function StatTile({
  label,
  value,
  hint,
  tone,
}: {
  label: string;
  value: React.ReactNode;
  hint?: string;
  tone?: "critical" | "warning";
}) {
  const icon =
    tone === "critical" ? (
      <AlertOctagon className="size-4 text-critical" aria-hidden />
    ) : tone === "warning" ? (
      <AlertTriangle className="size-4 text-warning" aria-hidden />
    ) : null;
  return (
    <div className="rounded-lg border border-line bg-surface px-4 py-3">
      <div className="flex items-center gap-1.5 text-xs text-ink-2">
        {icon}
        {label}
      </div>
      <div className="mt-1 text-3xl font-semibold text-ink">{value}</div>
      {hint && <div className="mt-0.5 text-xs text-ink-muted">{hint}</div>}
    </div>
  );
}

/** Alert level: icon + label + status colour, never colour alone. */
export function LevelBadge({ level }: { level: "warning" | "alert" }) {
  return level === "alert" ? (
    <span className="inline-flex items-center gap-1 rounded-full bg-critical/12 px-2 py-0.5 text-xs font-medium text-critical">
      <AlertOctagon className="size-3.5" aria-hidden /> Alert
    </span>
  ) : (
    <span className="inline-flex items-center gap-1 rounded-full bg-warning/15 px-2 py-0.5 text-xs font-medium text-ink">
      <AlertTriangle className="size-3.5 text-warning" aria-hidden /> Warning
    </span>
  );
}

const STATUS: Record<AlertStatus, { label: string; icon: React.ReactNode }> = {
  open: { label: "Open", icon: <CircleDot className="size-3.5" aria-hidden /> },
  investigating: { label: "Investigating", icon: <Search className="size-3.5" aria-hidden /> },
  resolved: { label: "Resolved", icon: <CheckCircle2 className="size-3.5 text-good" aria-hidden /> },
  false_positive: { label: "False positive", icon: <XCircle className="size-3.5" aria-hidden /> },
};

export function StatusPill({ status }: { status: AlertStatus }) {
  const s = STATUS[status];
  return (
    <span className="inline-flex items-center gap-1 rounded-full border border-line px-2 py-0.5 text-xs text-ink-2">
      {s.icon}
      {s.label}
    </span>
  );
}

export const STATUS_OPTIONS: { value: AlertStatus; label: string }[] = (
  Object.keys(STATUS) as AlertStatus[]
).map((value) => ({ value, label: STATUS[value].label }));

/** Risk score 0..1 as a thin bar on the single-hue (magnitude) ramp, with the number beside it. */
export function RiskBar({ value, threshold }: { value: number; threshold?: number | null }) {
  const over = threshold != null && value >= threshold;
  return (
    <span className="inline-flex items-center gap-2">
      <span className="relative h-1.5 w-20 overflow-hidden rounded-full bg-surface-2">
        <span
          className="absolute inset-y-0 left-0 rounded-full"
          style={{
            width: `${Math.max(2, value * 100)}%`,
            background: over ? "var(--critical)" : "var(--risk-mid)",
          }}
        />
      </span>
      <span className="tabular text-xs text-ink-2">{value.toFixed(3)}</span>
    </span>
  );
}

export function Button({
  variant = "primary",
  className = "",
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "ghost" }) {
  const styles =
    variant === "primary"
      ? "bg-accent text-white hover:opacity-90"
      : "border border-line text-ink hover:bg-surface-2";
  return (
    <button
      className={`inline-flex items-center justify-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-50 ${styles} ${className}`}
      {...props}
    />
  );
}

export function Empty({ children }: { children: React.ReactNode }) {
  return <p className="py-8 text-center text-sm text-ink-muted">{children}</p>;
}

export function ErrorNote({ error }: { error: unknown }) {
  const message = error instanceof Error ? error.message : "Something went wrong";
  return (
    <p className="rounded-md border border-critical/30 bg-critical/8 px-3 py-2 text-sm text-ink">
      <AlertOctagon className="mr-1.5 inline size-4 text-critical" aria-hidden />
      {message}
    </p>
  );
}
