"""Side-by-side comparison of every model report under ml/reports/.

python -m botgraph_ml.compare   # writes ml/reports/comparison.md
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from botgraph_ml.config import load_params, repo_path

ORDER = ["baseline", "graphsage", "e_graphsage", "gatv2"]


def _fmt(value: Any, digits: int = 3) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def load_reports(reports_dir: Path) -> dict[str, dict[str, Any]]:
    reports = {}
    for name in ORDER:
        path = reports_dir / name / "metrics.json"
        if path.exists():
            reports[name] = json.loads(path.read_text())
    return reports


def render(reports: dict[str, dict[str, Any]]) -> str:
    lines = [
        "# Model comparison (held-out botnet families)",
        "",
        "Window level: one row per (host, 5-minute window).",
        "Threshold chosen on validation families only.",
        "",
        "| Model | Precision | Recall | F1 | PR-AUC | FPR @ 95% recall | Latency p95 (ms/window) |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in reports.values():
        w = r["test"]["window"]
        latency = r.get("latency_ms_per_window", {}).get("p95")
        lines.append(
            f"| {r['model']} | {_fmt(w['precision'])} | {_fmt(w['recall'])} | {_fmt(w['f1'])} "
            f"| {_fmt(w['pr_auc'])} | {_fmt(w['fpr_at_95_recall'])} | {_fmt(latency, 1)} |"
        )

    lines += [
        "",
        "## Per family (host level)",
        "",
        "| Model | Family | Bots detected | Host F1 | Median time to detect |",
        "|---|---|---|---|---|",
    ]
    for r in reports.values():
        for s in r["test"]["per_scenario"].values():
            ttd = s["median_time_to_detect_s"]
            detect = "n/a" if ttd is None else f"{ttd / 60:.0f} min"
            lines.append(
                f"| {r['model']} | {s['family']} | {s['bots_detected']}/{s['bots_total']} "
                f"| {_fmt(s['host']['f1'])} | {detect} |"
            )
    return "\n".join(lines) + "\n"


def main() -> None:
    reports_dir = repo_path(load_params()["data"]["reports_dir"])
    reports = load_reports(reports_dir)
    if not reports:
        raise SystemExit(f"no reports found in {reports_dir}; train a model first")
    out = reports_dir / "comparison.md"
    out.write_text(render(reports))
    print(out.read_text())


if __name__ == "__main__":
    main()
