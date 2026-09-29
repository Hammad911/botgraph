"use client";

import { createContext, useCallback, useContext, useMemo, useSyncExternalStore } from "react";
import type { Role } from "./api";

// The session lives in sessionStorage: it survives reloads but not closing the tab, and it is
// never sent automatically (no CSRF surface). Production would move this to an HttpOnly cookie.
const KEY = "botgraph.session";
const RANK: Record<Role, number> = { viewer: 0, analyst: 1, admin: 2 };

export type Session = { token: string; username: string; role: Role };

type AuthContext = {
  session: Session | null;
  ready: boolean;
  signIn: (session: Session) => void;
  signOut: () => void;
  can: (role: Role) => boolean;
};

const Ctx = createContext<AuthContext | null>(null);
const listeners = new Set<() => void>();

function read(): string | null {
  return typeof window === "undefined" ? null : window.sessionStorage.getItem(KEY);
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function write(value: Session | null) {
  if (value) window.sessionStorage.setItem(KEY, JSON.stringify(value));
  else window.sessionStorage.removeItem(KEY);
  listeners.forEach((l) => l());
}

/** Drop the session (an expired or revoked token); the console layout then redirects to login. */
export function clearSession() {
  if (typeof window !== "undefined") write(null);
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const raw = useSyncExternalStore(subscribe, read, () => null);
  // On the server (and the first client pass) storage is unknown: `ready` gates redirects.
  const ready = useSyncExternalStore(
    subscribe,
    () => true,
    () => false,
  );
  const session = useMemo<Session | null>(() => {
    if (!raw) return null;
    try {
      return JSON.parse(raw) as Session;
    } catch {
      return null;
    }
  }, [raw]);

  const signIn = useCallback((s: Session) => write(s), []);
  const signOut = useCallback(() => write(null), []);
  const can = useCallback((role: Role) => !!session && RANK[session.role] >= RANK[role], [session]);

  const value = useMemo(
    () => ({ session, ready, signIn, signOut, can }),
    [session, ready, signIn, signOut, can],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthContext {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}
