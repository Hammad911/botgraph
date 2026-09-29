"use client";

import {
  AlertOctagon,
  BellRing,
  LayoutDashboard,
  LogOut,
  Network,
  Radar,
  ScrollText,
  Share2,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { endpoints, type Alert, type Role } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { sensorLabel } from "@/lib/format";
import { useLive, type LiveState } from "@/lib/live";

const NAV: { href: string; label: string; icon: typeof Radar; role?: Role }[] = [
  { href: "/", label: "Overview", icon: LayoutDashboard },
  { href: "/alerts", label: "Alerts", icon: BellRing },
  { href: "/map", label: "Live map", icon: Share2 },
  { href: "/sensors", label: "Sensors", icon: Radar },
  { href: "/audit", label: "Audit log", icon: ScrollText, role: "admin" },
];

function LiveDot({ state }: { state: LiveState }) {
  const label = { live: "Live", connecting: "Connecting…", offline: "Offline" }[state];
  const color = { live: "var(--good)", connecting: "var(--warning)", offline: "var(--ink-muted)" }[state];
  return (
    <span className="flex items-center gap-1.5 text-xs text-ink-2" aria-live="polite">
      <span className="size-2 rounded-full" style={{ background: color }} aria-hidden />
      {label}
    </span>
  );
}

export default function ConsoleLayout({ children }: { children: React.ReactNode }) {
  const { session, ready, signOut, can } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  const [toast, setToast] = useState<Alert | null>(null);

  const onAlert = useCallback((a: Alert) => {
    if (a.level === "alert") setToast(a);
  }, []);
  const live = useLive(session?.token ?? null, onAlert);

  useEffect(() => {
    if (ready && !session) router.replace("/login");
  }, [ready, session, router]);

  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(() => setToast(null), 8000);
    return () => clearTimeout(t);
  }, [toast]);

  if (!ready || !session) return null;

  return (
    <div className="flex min-h-screen">
      <aside className="flex w-56 shrink-0 flex-col border-r border-line bg-surface">
        <div className="flex items-center gap-2 px-4 py-4">
          <Network className="size-5 text-accent" aria-hidden />
          <span className="font-semibold">BotGraph</span>
        </div>
        <nav className="flex-1 space-y-0.5 px-2">
          {NAV.filter((n) => !n.role || can(n.role)).map(({ href, label, icon: Icon }) => {
            const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
            return (
              <Link
                key={href}
                href={href}
                className={`flex items-center gap-2 rounded-md px-3 py-2 text-sm ${
                  active ? "bg-surface-2 font-medium text-ink" : "text-ink-2 hover:bg-surface-2"
                }`}
              >
                <Icon className="size-4" aria-hidden />
                {label}
              </Link>
            );
          })}
        </nav>
        <div className="space-y-3 border-t border-line p-4">
          <LiveDot state={live} />
          <div className="text-xs text-ink-2">
            {session.username} <span className="text-ink-muted">· {session.role}</span>
          </div>
          <button
            onClick={() => {
              // Revoke the token server-side too; signing out locally must not wait on it.
              endpoints.logout(session.token).catch(() => undefined);
              signOut();
              router.replace("/login");
            }}
            className="flex items-center gap-1.5 text-xs text-ink-2 hover:text-ink"
          >
            <LogOut className="size-3.5" aria-hidden /> Sign out
          </button>
        </div>
      </aside>

      <main className="min-w-0 flex-1 px-6 py-6">{children}</main>

      {toast && (
        <Link
          href={`/alerts/${toast.id}`}
          role="status"
          className="fixed right-4 bottom-4 flex max-w-sm items-start gap-3 rounded-lg border border-critical/40 bg-surface p-4 shadow-lg"
        >
          <AlertOctagon className="mt-0.5 size-5 shrink-0 text-critical" aria-hidden />
          <span className="text-sm">
            <span className="font-semibold">New alert:</span> {toast.ip} on {sensorLabel(toast.sensor_id)}
            <span className="block text-xs text-ink-muted">
              flagged in {toast.hits} of the last {toast.rule_n} windows · open details
            </span>
          </span>
        </Link>
      )}
    </div>
  );
}
