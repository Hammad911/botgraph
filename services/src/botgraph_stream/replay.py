"""Replay a recorded capture onto the bus as if it were live traffic.

    ctu13:<scenario>   e.g. ctu13:6   (prepared flows in ml/data/processed/ctu13/flows/)
    iot23:<capture>    e.g. iot23:CTU-IoT-Malware-Capture-34-1

Flows are published to ``flows.raw`` in timestamp order, in batches. Event time is preserved
(the detector windows on flow timestamps), so ``speed`` only changes wall-clock pacing:
``speed=60`` plays an hour in a minute; ``speed=0`` goes as fast as the pipeline can take it.

Labels are stripped before publishing, so the pipeline never sees ground truth. The replay
keeps host-level truth separately so a run can be scored afterwards.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

import pandas as pd

from botgraph_core import Label
from botgraph_ml.config import load_labels, load_params, repo_path
from botgraph_ml.flow_labels import window_host_labels
from botgraph_stream.bus import FLOWS_RAW, Bus


@dataclass(frozen=True)
class ReplaySource:
    dataset: str
    name: str
    flows: pd.DataFrame  # sorted by ts
    truth: dict[str, Label]  # host-level ground truth (never published)
    internal_nets: tuple[str, ...]

    @property
    def sensor_id(self) -> str:
        return f"replay-{self.dataset}-{self.name}"


def load_source(spec: str, params: dict[str, Any] | None = None) -> ReplaySource:
    """Parse ``dataset:name`` and load its prepared flows and ground truth."""
    params = params or load_params()
    dataset, _, name = spec.partition(":")
    processed = repo_path(params["data"]["processed_dir"])
    if dataset == "ctu13":
        path = processed / "ctu13" / "flows" / f"scenario={int(name)}.parquet"
        labels = load_labels(repo_path(params["ctu13"]["labels"]))
        truth = labels.scenarios[int(name)].host_labels()
    elif dataset == "iot23":
        path = processed / "iot23" / "flows" / f"{name}.parquet"
        truth = {}
    else:
        raise SystemExit(f"unknown replay source {spec!r}; use ctu13:<n> or iot23:<capture>")
    if not path.exists():
        raise SystemExit(f"{path} not found; run the download + prepare steps first")
    nets = tuple(params[dataset]["internal_nets"])
    flows = pd.read_parquet(path).sort_values("ts", kind="stable").reset_index(drop=True)
    if dataset == "iot23":
        # IoT-23 labels flows, not hosts: a device that ever originated a malicious flow is a
        # bot; one whose traffic was all benign is benign (same rules as the offline test).
        truth = window_host_labels(flows, nets)
    return ReplaySource(dataset, name, flows, truth, nets)


class Replayer:
    def __init__(
        self, bus: Bus, source: ReplaySource, speed: float = 0.0, batch_flows: int = 500
    ) -> None:
        self.bus = bus
        self.source = source
        self.speed = speed
        self.batch_flows = batch_flows
        self.published = 0
        self._wall_start: float | None = None
        self._ts0 = float(source.flows["ts"].iloc[0]) if len(source.flows) else 0.0

    @property
    def done(self) -> bool:
        return self.published >= len(self.source.flows)

    @property
    def event_time(self) -> float:
        """Timestamp of the last published flow."""
        if self.published == 0:
            return self._ts0
        return float(self.source.flows["ts"].iloc[self.published - 1])

    def step(self) -> int:
        """Publish the next batch (sleeping first if pacing requires); returns flows sent."""
        if self.done:
            return 0
        batch = self.source.flows.iloc[self.published : self.published + self.batch_flows]
        if self.speed > 0:
            if self._wall_start is None:
                self._wall_start = time.monotonic()
            due = self._wall_start + (float(batch["ts"].iloc[0]) - self._ts0) / self.speed
            delay = due - time.monotonic()
            if delay > 0:
                time.sleep(min(delay, 1.0))  # stay responsive; the rest waits for next step
                if due - time.monotonic() > 0:
                    return 0
        records = json.loads(batch.assign(label=Label.UNKNOWN.value).to_json(orient="records"))
        sensor = self.source.sensor_id
        for record in records:
            record["sensor_id"] = sensor
        self.bus.publish(FLOWS_RAW, sensor, {"sensor_id": sensor, "flows": records})
        self.published += len(records)
        return len(records)
