import path from "node:path";
import type { NextConfig } from "next";

// REST calls go through Next's rewrite to the FastAPI server, so the browser stays same-origin.
// The WebSocket connects to the API directly (NEXT_PUBLIC_BOTGRAPH_WS_URL).
const api = process.env.BOTGRAPH_API_URL ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  // The e2e run builds into its own directory so it never clobbers a running `next dev`.
  distDir: process.env.NEXT_DIST_DIR ?? ".next",
  // The dev-mode badge sits over the sidebar's sign-out link.
  devIndicators: false,
  // Pin the project root (a stray lockfile in a parent directory would otherwise be picked up).
  turbopack: { root: path.resolve(__dirname) },
  outputFileTracingRoot: path.resolve(__dirname),
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${api}/api/:path*` }];
  },
};

export default nextConfig;
