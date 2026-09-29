import type { NextConfig } from "next";

// REST calls go through Next's rewrite to the FastAPI server, so the browser stays same-origin.
// The WebSocket connects to the API directly (NEXT_PUBLIC_BOTGRAPH_WS_URL).
const api = process.env.BOTGRAPH_API_URL ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${api}/api/:path*` }];
  },
};

export default nextConfig;
