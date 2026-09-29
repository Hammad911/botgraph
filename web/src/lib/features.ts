// Plain-language names for the model's host and flow features (packages/botgraph-core graph.py),
// so explanations read as sentences an analyst can act on. `log` features are shown de-logged.

// `decimal`: a de-logged quantity that is not a count (seconds, averages); counts are whole.
type Feature = { label: string; log?: boolean; percent?: boolean; decimal?: boolean };

export const NODE_FEATURES: Record<string, Feature> = {
  is_internal: { label: "Inside the monitored network" },
  log_out_degree: { label: "Distinct hosts contacted", log: true },
  log_in_degree: { label: "Distinct hosts contacting it", log: true },
  log_out_flows: { label: "Connections made", log: true },
  log_in_flows: { label: "Connections received", log: true },
  log_unique_dst_ports: { label: "Distinct destination ports (scanning)", log: true },
  log_bytes_sent: { label: "Bytes sent", log: true },
  log_bytes_recv: { label: "Bytes received", log: true },
  sent_ratio: { label: "Share of traffic it sends", percent: true },
  failed_ratio: { label: "Failed connections", percent: true },
  frac_dns: { label: "DNS share of connections", percent: true },
  frac_smtp: { label: "Email (SMTP) share of connections", percent: true },
  frac_irc: { label: "IRC share of connections (classic C2)", percent: true },
  periodicity: { label: "Regular, beacon-like timing", percent: true },
  dst_entropy: { label: "Spread across destinations", percent: true },
};

export const EDGE_FEATURES: Record<string, Feature> = {
  log_flows: { label: "Connections", log: true },
  log_bytes: { label: "Bytes", log: true },
  log_mean_bytes: { label: "Bytes per connection", log: true, decimal: true },
  log_pkts: { label: "Packets", log: true },
  log_mean_duration: { label: "Seconds per connection", log: true, decimal: true },
  log_dst_ports: { label: "Ports used", log: true },
  frac_tcp: { label: "TCP share", percent: true },
  frac_udp: { label: "UDP share", percent: true },
  frac_icmp: { label: "ICMP share", percent: true },
  failed_ratio: { label: "Failed connections", percent: true },
  log_iat_mean: { label: "Seconds between connections", log: true, decimal: true },
  periodicity: { label: "Beacon-like regularity", percent: true },
};

export function describe(table: Record<string, Feature>, name: string, value: number) {
  const f = table[name] ?? { label: name };
  let shown: string;
  if (f.log) {
    const raw = Math.expm1(value);
    shown =
      f.decimal && raw < 100 ? raw.toFixed(raw < 10 ? 1 : 0) : Math.round(raw).toLocaleString("en");
  } else if (f.percent) {
    shown = `${Math.round(value * 100)}%`;
  } else {
    shown = value >= 1 ? "yes" : "no";
  }
  return { label: f.label, value: shown };
}
