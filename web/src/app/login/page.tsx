"use client";

import { Network } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Button, ErrorNote } from "@/components/ui";
import { endpoints } from "@/lib/api";
import { useAuth } from "@/lib/auth";

export default function LoginPage() {
  const { signIn } = useAuth();
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const t = await endpoints.login(username, password);
      signIn({ token: t.access_token, username: t.username, role: t.role });
      router.replace("/");
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center px-4">
      <form onSubmit={submit} className="w-full max-w-sm space-y-4 rounded-lg border border-line bg-surface p-6">
        <div className="flex items-center gap-2">
          <Network className="size-6 text-accent" aria-hidden />
          <div>
            <h1 className="text-lg font-semibold">BotGraph</h1>
            <p className="text-xs text-ink-muted">Botnet detection console</p>
          </div>
        </div>
        <label className="block text-sm">
          <span className="text-ink-2">Username</span>
          <input
            className="mt-1 w-full rounded-md border border-line bg-page px-3 py-2 outline-none focus:border-accent"
            autoComplete="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
          />
        </label>
        <label className="block text-sm">
          <span className="text-ink-2">Password</span>
          <input
            type="password"
            className="mt-1 w-full rounded-md border border-line bg-page px-3 py-2 outline-none focus:border-accent"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </label>
        {error != null && <ErrorNote error={error} />}
        <Button type="submit" className="w-full" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </Button>
      </form>
    </main>
  );
}
