import path from "node:path";
import type { NextConfig } from "next";

// REST calls go through Next's rewrite to the FastAPI server, so the browser stays same-origin.
// The WebSocket goes to NEXT_PUBLIC_BOTGRAPH_WS_URL if set, otherwise same-origin /api/ws.
const api = process.env.BOTGRAPH_API_URL ?? "http://127.0.0.1:8000";
const isDev = process.env.NODE_ENV === "development";
const wsOrigin = (() => {
  const configured = process.env.NEXT_PUBLIC_BOTGRAPH_WS_URL;
  if (configured) return new URL(configured).origin.replace(/^http/, "ws");
  return isDev ? "ws://127.0.0.1:8000" : "";
})();

// Static CSP (no nonces, so pages stay statically rendered). Inline scripts and styles are
// needed by Next's hydration and by Recharts/Sigma; everything else is locked to this origin.
// React needs eval only in development.
const csp = [
  "default-src 'self'",
  `script-src 'self' 'unsafe-inline'${isDev ? " 'unsafe-eval'" : ""}`,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' blob: data:",
  "font-src 'self'",
  `connect-src 'self'${wsOrigin ? ` ${wsOrigin}` : ""}`,
  "worker-src 'self' blob:",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
].join("; ");

const nextConfig: NextConfig = {
  // The e2e run builds into its own directory so it never clobbers a running `next dev`.
  distDir: process.env.NEXT_DIST_DIR ?? ".next",
  // A self-contained server for the container image (deploy/web.Dockerfile sets NEXT_OUTPUT);
  // `next start` (dev, e2e) does not work with standalone output.
  output: process.env.NEXT_OUTPUT === "standalone" ? "standalone" : undefined,
  poweredByHeader: false,
  // The dev-mode badge sits over the sidebar's sign-out link.
  devIndicators: false,
  // Pin the project root (a stray lockfile in a parent directory would otherwise be picked up).
  turbopack: { root: path.resolve(__dirname) },
  outputFileTracingRoot: path.resolve(__dirname),
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${api}/api/:path*` }];
  },
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "Content-Security-Policy", value: csp },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "no-referrer" },
          { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
        ],
      },
    ];
  },
};

export default nextConfig;
