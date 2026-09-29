import { defineConfig } from "@playwright/test";

// End-to-end against the real stack: FastAPI on a seeded SQLite store + a production build of
// the console. Ports are separate from the usual dev servers (8000 / 3000).
const API_PORT = 8010;
const WEB_PORT = 3010;
// Relative to the repo root (the API server's cwd). A fresh checkout has no data/ yet.
const DB = process.env.E2E_DB_URL ?? "sqlite:///data/e2e.db";
export const E2E_USER = { username: "demo", password: "e2e-password-123" };

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["github"], ["list"]] : "list",
  use: { baseURL: `http://localhost:${WEB_PORT}`, trace: "retain-on-failure" },
  webServer: [
    {
      command:
        `uv run botgraph-api --db ${DB} seed-demo --fresh --username ${E2E_USER.username} ` +
        `--password ${E2E_USER.password} && uv run botgraph-api --db ${DB} serve --port ${API_PORT}`,
      cwd: "..",
      // The WebSocket is refused from origins the API does not list.
      env: { BOTGRAPH_CORS_ORIGINS: `http://localhost:${WEB_PORT}`, BOTGRAPH_LOG_LEVEL: "WARNING" },
      url: `http://127.0.0.1:${API_PORT}/api/health`,
      timeout: 180_000,
      reuseExistingServer: false,
    },
    {
      command: `npm run build && npx next start --port ${WEB_PORT}`,
      url: `http://localhost:${WEB_PORT}/login`,
      timeout: 300_000,
      reuseExistingServer: false,
      env: {
        NEXT_DIST_DIR: ".next-e2e",
        BOTGRAPH_API_URL: `http://127.0.0.1:${API_PORT}`,
        NEXT_PUBLIC_BOTGRAPH_WS_URL: `ws://127.0.0.1:${API_PORT}/api/ws`,
      },
    },
  ],
});
