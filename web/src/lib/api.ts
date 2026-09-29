import { z } from "zod";

// Schemas mirror api/src/botgraph_api/schemas.py. Parsing every response means a contract drift
// surfaces as a clear error here instead of `undefined` deep inside a component.

export const Role = z.enum(["viewer", "analyst", "admin"]);
export type Role = z.infer<typeof Role>;

export const AlertStatus = z.enum(["open", "investigating", "resolved", "false_positive"]);
export type AlertStatus = z.infer<typeof AlertStatus>;

export const Token = z.object({
  access_token: z.string(),
  token_type: z.literal("bearer"),
  username: z.string(),
  role: Role,
});
export type Token = z.infer<typeof Token>;

export const Alert = z.object({
  id: z.number(),
  sensor_id: z.string(),
  ip: z.string(),
  level: z.enum(["warning", "alert"]),
  window_start: z.number(),
  score: z.number(),
  hits: z.number(),
  rule_k: z.number(),
  rule_n: z.number(),
  threshold: z.number(),
  raised_at: z.string(),
  cleared_window_start: z.number().nullable(),
  status: AlertStatus,
  assignee: z.string().nullable(),
  note: z.string().nullable(),
  updated_at: z.string().nullable(),
});
export type Alert = z.infer<typeof Alert>;

const ExplainedFlow = z.object({
  src: z.string(),
  dst: z.string(),
  importance: z.number(),
  features: z.record(z.string(), z.number()),
});
const ExplainedFeature = z.object({
  feature: z.string(),
  importance: z.number(),
  value: z.number(),
});
export const Explanation = z.object({
  ip: z.string(),
  score: z.number(),
  top_flows: z.array(ExplainedFlow),
  top_features: z.array(ExplainedFeature),
});
export type Explanation = z.infer<typeof Explanation>;

export const AlertDetail = Alert.extend({ explanation: Explanation.nullable() });
export type AlertDetail = z.infer<typeof AlertDetail>;

export const Sensor = z.object({
  id: z.string(),
  state: z.enum(["learning", "active"]),
  threshold: z.number().nullable(),
  model_threshold: z.number().nullable(),
  baseline_scores: z.number(),
  calibrated_at: z.string().nullable(),
  last_window_start: z.number().nullable(),
});
export type Sensor = z.infer<typeof Sensor>;

export const Overview = z.object({
  latest_window_start: z.number().nullable(),
  sensors: z.number(),
  active_sensors: z.number(),
  windows_scored: z.number(),
  hosts_monitored: z.number(),
  open_alerts: z.number(),
  open_warnings: z.number(),
  trend: z.array(z.object({ hour_start: z.number(), warnings: z.number(), alerts: z.number() })),
  top_hosts: z.array(
    z.object({
      sensor_id: z.string(),
      ip: z.string(),
      peak_score: z.number(),
      open_alert: z.boolean(),
    }),
  ),
});
export type Overview = z.infer<typeof Overview>;

export const Host = z.object({
  sensor_id: z.string(),
  ip: z.string(),
  threshold: z.number().nullable(),
  timeline: z.array(z.object({ window_start: z.number(), score: z.number() })),
  alerts: z.array(Alert),
});
export type Host = z.infer<typeof Host>;

export const GraphNode = z.object({
  id: z.string(),
  score: z.number(),
  internal: z.boolean(),
  flagged: z.boolean(),
  degree: z.number(),
});
export const GraphEdge = z.object({
  source: z.string(),
  target: z.string(),
  flows: z.number(),
  bytes: z.number(),
  periodicity: z.number(),
});
export const GraphSnapshot = z.object({
  sensor_id: z.string(),
  window_id: z.string(),
  window_start: z.number(),
  graph: z.object({
    nodes: z.array(GraphNode),
    edges: z.array(GraphEdge),
    total_nodes: z.number(),
    total_edges: z.number(),
  }),
});
export type GraphSnapshot = z.infer<typeof GraphSnapshot>;

const DriftVersus = z.object({
  features: z.record(z.string(), z.number()),
  max_feature: z.string(),
  max_psi: z.number(),
  score_psi: z.number(),
});
export type DriftVersus = z.infer<typeof DriftVersus>;

export const Drift = z.object({
  sensor_id: z.string(),
  window_start: z.number(),
  windows: z.number(),
  hosts: z.number(),
  baseline: DriftVersus.nullable(),
  training: DriftVersus.nullable(),
});
export type Drift = z.infer<typeof Drift>;

export const AuditEvent = z.object({
  id: z.number(),
  at: z.string(),
  actor: z.string().nullable(),
  action: z.string(),
  target: z.string().nullable(),
  client: z.string().nullable(),
  detail: z.record(z.string(), z.unknown()).nullable(),
});
export type AuditEvent = z.infer<typeof AuditEvent>;

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

export async function apiFetch<T>(
  path: string,
  schema: z.ZodType<T>,
  token: string | null,
  init: RequestInit = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.body) headers.set("Content-Type", "application/json");
  const res = await fetch(path, { ...init, headers });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail);
  }
  return schema.parse(await res.json());
}

export const endpoints = {
  login: (username: string, password: string) =>
    apiFetch("/api/auth/login", Token, null, {
      method: "POST",
      body: JSON.stringify({ username, password }),
    }),
  overview: (t: string) => apiFetch("/api/overview", Overview, t),
  sensors: (t: string) => apiFetch("/api/sensors", z.array(Sensor), t),
  alerts: (t: string, params: Record<string, string>) =>
    apiFetch(`/api/alerts?${new URLSearchParams(params)}`, z.array(Alert), t),
  alert: (t: string, id: number) => apiFetch(`/api/alerts/${id}`, AlertDetail, t),
  triage: (t: string, id: number, body: Partial<Pick<Alert, "status" | "assignee" | "note">>) =>
    apiFetch(`/api/alerts/${id}`, AlertDetail, t, { method: "PATCH", body: JSON.stringify(body) }),
  host: (t: string, sensor: string, ip: string) =>
    apiFetch(`/api/hosts/${encodeURIComponent(sensor)}/${encodeURIComponent(ip)}`, Host, t),
  graph: (t: string, sensor: string) =>
    apiFetch(`/api/graph/${encodeURIComponent(sensor)}`, GraphSnapshot, t),
  logout: (t: string) =>
    apiFetch("/api/auth/logout", z.object({ status: z.string() }), t, { method: "POST" }),
  drift: (t: string) => apiFetch("/api/drift", z.array(Drift), t),
  audit: (t: string) => apiFetch("/api/audit", z.array(AuditEvent), t),
  recalibrate: (t: string, sensor: string) =>
    apiFetch(`/api/sensors/${encodeURIComponent(sensor)}/recalibrate`, z.object({ status: z.string() }), t, {
      method: "POST",
    }),
};
