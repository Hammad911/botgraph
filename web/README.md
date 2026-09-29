# BotGraph console

Next.js 16 analyst console for BotGraph: overview, live network map, alert triage with
explanations, host timelines and sensors. It talks to the FastAPI backend (`api/`).

```bash
# 1. data: run the pipeline into the default store (data/botgraph.db)
uv run botgraph run --replay ctu13:6 --speed 60
# 2. API + an account
uv run botgraph-api create-user --username you --role admin
uv run botgraph-api serve                    # http://127.0.0.1:8000
# 3. console
cd web && npm install && npm run dev         # http://localhost:3000
```

- REST goes through Next's `/api` rewrite (`BOTGRAPH_API_URL`, default `http://127.0.0.1:8000`).
- Live updates use a WebSocket straight to the API (`NEXT_PUBLIC_BOTGRAPH_WS_URL`).
- Response shapes are validated with Zod (`src/lib/api.ts`), mirroring `api/.../schemas.py`.
- Charts follow a validated palette: status colours always carry an icon and label, risk uses a
  single-hue ramp, and every chart has a table view.
