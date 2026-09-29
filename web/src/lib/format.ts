// All event times are epoch seconds of the *traffic* (a replay can be from 2011), shown in UTC.
export function eventTime(ts: number | null | undefined): string {
  if (ts == null) return "n/a";
  return new Date(ts * 1000).toISOString().slice(0, 16).replace("T", " ") + " UTC";
}

export function hourLabel(ts: number): string {
  return new Date(ts * 1000).toISOString().slice(11, 16);
}

export function score(v: number): string {
  return v.toFixed(3);
}

export function compact(n: number): string {
  return new Intl.NumberFormat("en", { notation: "compact", maximumFractionDigits: 1 }).format(n);
}

export function sensorLabel(id: string): string {
  return id.replace(/^replay-/, "");
}
