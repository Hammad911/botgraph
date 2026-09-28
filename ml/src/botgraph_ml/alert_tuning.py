"""Choose the host alert rule on validation data only, then report it on test data.

    python -m botgraph_ml.alert_tuning   # writes ml/reports/alert_tuning.{md,json}

Uses the per-window scores saved by every leave-one-family-out fold. For each model, every
candidate rule ("flagged in >= k of the last n windows") is scored on the *validation*
families pooled across folds. The chosen rule must alert on at least MIN_BOT_RECALL of
validation bots; among those, the fewest false alerts on normal hosts wins, then the
fastest median time to alert. The single chosen rule is then applied to the test folds.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from botgraph_ml.config import load_params, repo_path
from botgraph_ml.metrics import AlertRule, host_alerts, json_safe

MODELS = ["baseline", "e_graphsage", "gatv2"]
NAMES = {"baseline": "XGBoost", "e_graphsage": "E-GraphSAGE", "gatv2": "GATv2"}
MIN_BOT_RECALL = 0.9
# Windows advance every minute, so n is also the look-back in minutes.
GRID = [
    AlertRule(k=k, n=n)
    for n in (5, 10, 15, 30, 60)
    for k in sorted({max(1, round(f * n)) for f in (0.2, 0.4, 0.6, 0.8, 1.0)})
]


@dataclass
class Outcome:
    bots: int = 0
    bots_total: int = 0
    benign: int = 0
    benign_total: int = 0
    times: list[float] = field(default_factory=list)

    @property
    def recall(self) -> float:
        return self.bots / self.bots_total if self.bots_total else 0.0

    @property
    def median_minutes(self) -> float | None:
        return statistics.median(self.times) / 60 if self.times else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "bots_alerted": self.bots,
            "bots_total": self.bots_total,
            "benign_alerted": self.benign,
            "benign_total": self.benign_total,
            "median_time_to_alert_min": self.median_minutes,
        }


@dataclass(frozen=True)
class Fold:
    family: str
    threshold: float
    scores: dict[str, pd.DataFrame]  # "val" / "test" -> per-window scores


def load_folds(cv_dir: Path) -> list[Fold]:
    folds = []
    for fold_dir in sorted(cv_dir.glob("lofo_*")):
        paths = {part: fold_dir / f"{part}_scores.parquet" for part in ("val", "test")}
        if not all(p.exists() for p in paths.values()):
            raise SystemExit(f"{fold_dir} has no saved scores; re-run it with --eval-only")
        folds.append(
            Fold(
                family=fold_dir.name.removeprefix("lofo_"),
                threshold=json.loads((fold_dir / "metrics.json").read_text())["threshold"],
                scores={part: pd.read_parquet(p) for part, p in paths.items()},
            )
        )
    return folds


def evaluate(folds: list[Fold], part: str, rule: AlertRule) -> Outcome:
    """Pool alert outcomes over folds; hosts are evaluated within each scenario."""
    out = Outcome()
    for fold in folds:
        for _, scenario in fold.scores[part].groupby("scenario"):
            hosts = host_alerts(scenario, fold.threshold, rule)
            bots, benign = hosts[hosts["y"] == 1], hosts[hosts["y"] == 0]
            out.bots += int(bots["alerted"].sum())
            out.bots_total += len(bots)
            out.benign += int(benign["alerted"].sum())
            out.benign_total += len(benign)
            out.times += bots["time_to_alert_s"].dropna().tolist()
    return out


def choose_rule(folds: list[Fold]) -> tuple[AlertRule, dict[AlertRule, Outcome]]:
    val = {rule: evaluate(folds, "val", rule) for rule in GRID}
    eligible = [r for r, o in val.items() if o.recall >= MIN_BOT_RECALL]
    pool = eligible or list(val)  # if nothing reaches the recall floor, maximise recall

    def key(r: AlertRule) -> tuple[float, ...]:
        o = val[r]
        return (
            -o.recall if not eligible else 0.0,
            o.benign,
            o.median_minutes if o.median_minutes is not None else float("inf"),
            r.n,
        )

    return min(pool, key=key), val


def _row(name: str, rule: AlertRule, o: Outcome) -> str:
    ttd = "n/a" if o.median_minutes is None else f"{o.median_minutes:.0f} min"
    return (
        f"| {name} | {rule.k} of {rule.n} | {o.bots}/{o.bots_total} "
        f"| {o.benign}/{o.benign_total} | {ttd} |"
    )


def main() -> None:
    reports_dir = repo_path(load_params()["data"]["reports_dir"])
    default = AlertRule(k=3, n=5)  # the original, untuned rule
    results: dict[str, Any] = {}
    lines = [
        "# Alert rule tuning",
        "",
        f"Rule chosen on validation families only: >= {MIN_BOT_RECALL:.0%} of validation bots"
        " alerted, then fewest normal-host alerts, then fastest alert.",
        "The chosen rule is then applied unchanged to the test families.",
        "",
        "| Model | Rule | Test bots alerted | Test normal hosts alerted | Median time to alert |",
        "|---|---|---|---|---|",
    ]
    for model in MODELS:
        cv_dir = reports_dir / "cv" / model
        if not cv_dir.is_dir():
            continue
        folds = load_folds(cv_dir)
        chosen, val = choose_rule(folds)
        test_default = evaluate(folds, "test", default)
        test_chosen = evaluate(folds, "test", chosen)
        lines.append(_row(f"{NAMES[model]} (default)", default, test_default))
        lines.append(_row(f"{NAMES[model]} (tuned)", chosen, test_chosen))
        results[model] = {
            "chosen_rule": {"k": chosen.k, "n": chosen.n},
            "val_chosen": val[chosen].to_dict(),
            "test_default": test_default.to_dict(),
            "test_chosen": test_chosen.to_dict(),
        }

    if not results:
        raise SystemExit(f"no CV folds found under {reports_dir / 'cv'}")
    (reports_dir / "alert_tuning.json").write_text(json.dumps(json_safe(results), indent=2))
    (reports_dir / "alert_tuning.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
