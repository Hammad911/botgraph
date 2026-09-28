"""Side-by-side comparison of every model report under ml/reports/.

    python -m botgraph_ml.compare   # writes ml/reports/comparison.md

Covers the main held-out split (ml/reports/<model>/) and, when present, the
leave-one-family-out cross-validation folds (ml/reports/cv/<model>/lofo_<Family>/).
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Any

from botgraph_ml.config import load_params, repo_path

ORDER = ["baseline", "graphsage", "e_graphsage", "gatv2"]
NAMES = {
    "baseline": "XGBoost",
    "xgboost_node_features": "XGBoost",
    "graphsage": "GraphSAGE",
    "e_graphsage": "E-GraphSAGE",
    "gatv2": "GATv2",
}


def _fmt(value: Any, digits: int = 3) -> str:
    return "n/a" if value is None else f"{value:.{digits}f}"


def _minutes(seconds: float | None) -> str:
    return "n/a" if seconds is None else f"{seconds / 60:.0f} min"


def load_reports(reports_dir: Path) -> dict[str, dict[str, Any]]:
    reports = {}
    for name in ORDER:
        path = reports_dir / name / "metrics.json"
        if path.exists():
            reports[name] = json.loads(path.read_text())
    return reports


def load_cv_reports(reports_dir: Path) -> dict[str, dict[str, dict[str, Any]]]:
    """{model: {test family: report}} for every finished CV fold."""
    cv: dict[str, dict[str, dict[str, Any]]] = {}
    for name in ORDER:
        for path in sorted((reports_dir / "cv" / name).glob("lofo_*/metrics.json")):
            family = path.parent.name.removeprefix("lofo_")
            cv.setdefault(name, {})[family] = json.loads(path.read_text())
    return cv


def _family_alerts(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Sum alert outcomes over each family's scenarios within one report."""
    out: dict[str, dict[str, Any]] = {}
    for s in report["test"]["per_scenario"].values():
        a = s["alerts"]
        agg = out.setdefault(
            s["family"], {"bots": 0, "bots_total": 0, "benign": 0, "benign_total": 0, "ttd": []}
        )
        agg["bots"] += a["bots_alerted"]
        agg["bots_total"] += a["bots_total"]
        agg["benign"] += a["benign_alerted"]
        agg["benign_total"] += a["benign_total"]
        if a["median_time_to_alert_s"] is not None:
            agg["ttd"].append(a["median_time_to_alert_s"])
    return out


def render(
    reports: dict[str, dict[str, Any]], cv: dict[str, dict[str, dict[str, Any]]] | None = None
) -> str:
    lines = [
        "# Model comparison",
        "",
        "## Main split (test families: Menti, Sogou, Murlo, NSIS.ay)",
        "",
        "Window level: one row per (host, 5-minute window). Threshold chosen on validation only.",
        "",
        "| Model | Precision | Recall | F1 | PR-AUC | FPR @ 95% recall | Latency p95 (ms/window) |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in reports.values():
        w = r["test"]["window"]
        latency = r.get("latency_ms_per_window", {}).get("p95")
        lines.append(
            f"| {NAMES.get(r['model'], r['model'])} | {_fmt(w['precision'])} "
            f"| {_fmt(w['recall'])} | {_fmt(w['f1'])} | {_fmt(w['pr_auc'])} "
            f"| {_fmt(w['fpr_at_95_recall'])} | {_fmt(latency, 1)} |"
        )

    rule = next(iter(reports.values()), {}).get("test", {}).get("alert_rule")
    rule_text = f"{rule['k']} of the last {rule['n']} windows" if rule else "the alert rule"
    lines += [
        "",
        f"### Alerts per family (a host alerts when flagged in {rule_text})",
        "",
        "| Model | Family | Bots alerted | Normal hosts alerted | Median time to alert |",
        "|---|---|---|---|---|",
    ]
    for r in reports.values():
        for s in r["test"]["per_scenario"].values():
            a = s["alerts"]
            lines.append(
                f"| {NAMES.get(r['model'], r['model'])} | {s['family']} "
                f"| {a['bots_alerted']}/{a['bots_total']} "
                f"| {a['benign_alerted']}/{a['benign_total']} "
                f"| {_minutes(a['median_time_to_alert_s'])} |"
            )

    if cv:
        lines += _render_cv(cv)
    return "\n".join(lines) + "\n"


def _render_cv(cv: dict[str, dict[str, dict[str, Any]]]) -> list[str]:
    lines = [
        "",
        "## Leave-one-family-out cross-validation",
        "",
        "Each botnet family is the test set once; validation uses a different family.",
        "Normal hosts are counted once per scenario (CTU-13 reuses the same 6 normal hosts).",
        "",
        "| Model | Family | PR-AUC | FPR @ 95% recall | Bots alerted | Normal hosts alerted "
        "| Median time to alert |",
        "|---|---|---|---|---|---|---|",
    ]
    summary = []
    for name, folds in cv.items():
        pr_aucs, bots, bots_total, benign, benign_total = [], 0, 0, 0, 0
        for family, report in sorted(folds.items()):
            w = report["test"]["window"]
            fa = _family_alerts(report).get(family)
            if fa is None:
                continue
            if w["pr_auc"] is not None:
                pr_aucs.append(w["pr_auc"])
            bots += fa["bots"]
            bots_total += fa["bots_total"]
            benign += fa["benign"]
            benign_total += fa["benign_total"]
            ttd = statistics.median(fa["ttd"]) if fa["ttd"] else None
            lines.append(
                f"| {NAMES.get(name, name)} | {family} | {_fmt(w['pr_auc'])} "
                f"| {_fmt(w['fpr_at_95_recall'])} | {fa['bots']}/{fa['bots_total']} "
                f"| {fa['benign']}/{fa['benign_total']} | {_minutes(ttd)} |"
            )
        spread = statistics.stdev(pr_aucs) if len(pr_aucs) > 1 else 0.0
        summary.append(
            f"| {NAMES.get(name, name)} | {len(folds)} "
            f"| {statistics.mean(pr_aucs):.3f} ± {spread:.3f} "
            f"| {bots}/{bots_total} | {benign}/{benign_total} |"
            if pr_aucs
            else f"| {NAMES.get(name, name)} | {len(folds)} | n/a | n/a | n/a |"
        )
    lines += [
        "",
        "### Summary across folds",
        "",
        "| Model | Folds | Mean PR-AUC ± std | Bots alerted | Normal hosts alerted |",
        "|---|---|---|---|---|",
        *summary,
    ]
    return lines


def main() -> None:
    reports_dir = repo_path(load_params()["data"]["reports_dir"])
    reports = load_reports(reports_dir)
    cv = load_cv_reports(reports_dir)
    if not reports and not cv:
        raise SystemExit(f"no reports found in {reports_dir}; train a model first")
    out = reports_dir / "comparison.md"
    out.write_text(render(reports, cv))
    print(out.read_text())


if __name__ == "__main__":
    main()
