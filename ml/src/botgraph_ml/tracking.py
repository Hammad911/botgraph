"""Thin MLflow wrapper. Logging is active only when ``MLFLOW_TRACKING_URI`` is set
(e.g. ``http://localhost:5000`` from the compose stack); otherwise every call is a no-op.
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

EXPERIMENT = "botgraph"


def _flatten(values: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for key, value in values.items():
        name = f"{prefix}{key}"
        if isinstance(value, Mapping):
            flat.update(_flatten(value, f"{name}."))
        elif isinstance(value, int | float) and not isinstance(value, bool):
            flat[name] = value
    return flat


class Tracker:
    def __init__(self, active: bool) -> None:
        self.active = active

    def log_params(self, params: Mapping[str, Any]) -> None:
        if self.active:
            import mlflow

            mlflow.log_params({k: str(v) for k, v in params.items()})

    def log_metrics(self, metrics: Mapping[str, Any], step: int | None = None) -> None:
        """Log every finite number in a (possibly nested) metrics dict."""
        if self.active:
            import mlflow

            flat = {k: float(v) for k, v in _flatten(metrics).items() if v == v}  # drop NaN
            mlflow.log_metrics(flat, step=step)

    def log_artifacts(self, directory: Path) -> None:
        if self.active:
            import mlflow

            mlflow.log_artifacts(str(directory), artifact_path=directory.name)


@contextmanager
def tracked_run(run_name: str, tags: Mapping[str, str] | None = None) -> Iterator[Tracker]:
    if not os.environ.get("MLFLOW_TRACKING_URI"):
        yield Tracker(active=False)
        return

    import mlflow

    mlflow.set_experiment(EXPERIMENT)
    with mlflow.start_run(run_name=run_name, tags=dict(tags or {})):
        yield Tracker(active=True)
