# BotGraph

**Botnet detection with graph neural networks.** BotGraph turns network flows into a
host-to-host communication graph every minute and uses a GNN trained in-house to flag infected
hosts. It targets the coordinated behaviour that per-flow detectors miss: C2 beaconing,
fan-out scanning and peer-to-peer bot meshes.

> Status: **Phases 0–4 done.** Three GNNs trained and evaluated on all 13 CTU-13 scenarios
> (leave-one-family-out CV) and on IoT-23 without retraining, plus a live detection pipeline
> (`botgraph run`). The web console comes next. See the [model card](docs/model_card.md).

## Results

Evaluated with **leave-one-family-out cross-validation** on CTU-13: each of the 7 botnet
families is the test set once and is never seen in training; decision thresholds and the alert
rule are chosen on a *different* validation family.

| Model | Mean PR-AUC (7 unseen families) | Bots alerted | Normal hosts falsely alerted |
|---|---|---|---|
| XGBoost (host features only) | 0.750 ± 0.19 | 30/35 | 11/78 |
| E-GraphSAGE | 0.844 ± 0.25 | 29/35 | 10/78 |
| **GATv2** | **0.870 ± 0.25** | **30/35** | **5/78** |

Alerts use the tuned rule "flagged in 12 of the last 15 minutes". With the same rule, **GATv2
catches as many bots as XGBoost with half the false alerts**, and it scores each 5-minute
window in ~10 ms on a laptop CPU.

Two alert levels are planned for the live system, both measured on the same folds:

| Level | Rule | GATv2 bots | GATv2 false alerts | Median time to alert |
|---|---|---|---|---|
| Warning | 3 of last 5 min | 34/35 | 22/78 | 2 min |
| Alert | 12 of last 15 min | 30/35 | 5/78 | 28 min |

### Cross-dataset test: IoT-23, no retraining

The CTU-13 models were applied unchanged (same weights and thresholds) to 14 IoT-23 captures:
a different network, IoT devices instead of PCs, and different malware (Mirai, Gafgyt, Hide and
Seek, Muhstik, Hakai, Torii, a Trojan) plus 3 benign IoT honeypots. Labels come from IoT-23's
per-flow labels ([`ml/reports/iot23/report.md`](ml/reports/iot23/report.md)).

| Model | Window PR-AUC | Infected devices alerted (12 of 15) | Benign devices alerted (12 of 15) |
|---|---|---|---|
| XGBoost | 0.470 | 9/13 | 6/24 |
| GraphSAGE | **0.974** | 11/13 | 6/24 |
| E-GraphSAGE | 0.931 | 12/13 | 6/24 |
| GATv2 | 0.962 | 11/13 | **5/24** |

What transfers and what does not:

- **Scanning botnets transfer almost perfectly.** On the Hide and Seek, Muhstik, Hakai and
  Mirai captures every GNN scores PR-AUC 0.99–1.00, while XGBoost on host features alone drops
  to 0.45–0.98. Fan-out scanning looks the same on any network, and the graph captures it.
  Gafgyt is harder (0.49–0.74), but its infected device is still alerted.
- **Normal IoT devices look like bots to a model trained on PCs.** The false alerts are a
  Philips Hue, an Amazon Echo and home routers, flagged in 50–92% of their windows. IoT devices
  heartbeat to cloud servers on a fixed schedule; on CTU-13's university PCs, that periodic
  pattern almost always meant C2 beaconing.
- **Stealthy, low-volume bots are not detected for the right reason.** Torii and the Trojan
  (14–18 malicious flows a day) were flagged mostly in windows *without* malicious traffic,
  i.e. for looking like IoT devices, not for their C2 traffic.

### Calibrating to a new network

Two ways to adapt to IoT with a short benign baseline were tested, rotating over the 3 benign
honeypots (calibrate on 2, test on the held-out one and all malware captures):

| Method | Held-out benign windows flagged | Benign devices alerted on malware networks | Infected devices alerted | CTU-13 PR-AUC |
|---|---|---|---|---|
| None | 31–91% | 2/10 | 11/13 | 0.878 |
| **Threshold at 95th pct of baseline** | **0–26%** | **0/10** | 6–10/13 | 0.878 (model unchanged) |
| Fine-tune on benign IoT¹ | worse in 2 of 3 folds (50→77%, 31→48%) | 1/8 → 1/8 | 8/9 → 9/9 | 0.82–0.86 (forgot) |

¹ Run on the first 11 captures, before the 3 large ones finished downloading.

**Threshold recalibration works; fine-tuning does not** (the re-selected threshold collapsed
and the model partly forgot CTU-13). After recalibration every scanning botnet (Hide and Seek,
Muhstik, both large Mirai captures, Gafgyt) is still alerted in every fold; the devices that
drop out are mostly the stealthy ones that were flagged for the wrong reason. Reports:
[`calibration_threshold.md`](ml/reports/iot23/calibration_threshold.md),
[`calibration_finetune.md`](ml/reports/iot23/calibration_finetune.md).

**Takeaway:** BotGraph detects noisy botnet behaviour on networks it has never seen, but it
needs a short learning period on a new network's normal traffic before its alerts are trusted,
and it does not reliably detect low-volume, stealthy C2.

**Limitations.** CTU-13 has only 6 labelled normal hosts (the same ones in every scenario), so
the false-alert numbers come from a small population. The peer-to-peer family NSIS.ay is hard
for every model (PR-AUC 0.30–0.42); short-lived bots (Sogou, a 26-minute capture) can finish
before the strict alert rule fires. Full details: [`ml/reports/comparison.md`](ml/reports/comparison.md),
[`ml/reports/alert_tuning.md`](ml/reports/alert_tuning.md).

## How it works

```
flows (Zeek / NetFlow / CTU-13 / IoT-23)
   └─▶ normalize to one FlowRecord schema
         └─▶ 5-min sliding windows (1-min hop)
               └─▶ directed host graph + node/edge features
                     └─▶ GNN node classification ─▶ host risk score ─▶ alert + explanation
```

Node features include fan-out degree, distinct destination ports, failed-connection ratio,
bytes sent/received, DNS/SMTP/IRC activity, **beacon periodicity** (regularity of
inter-arrival times) and destination entropy. Edges carry aggregated flow statistics.

Training and live inference use the same feature code (`packages/botgraph-core`), so the
features the model sees in production match what it was trained on.

## Repository layout

| Path | Purpose |
|---|---|
| `packages/botgraph-core` | Flow schema, dataset adapters, windowing, graph features |
| `ml/` | Data prep, labels, models, training and evaluation |
| `services/` | Live pipeline: ingest, streaming windows, detector, alerts, replay (`botgraph` CLI) |
| `web/` | Next.js analyst console *(planned)* |
| `deploy/` | Dockerfile; compose with Redpanda, Postgres, ClickHouse, Redis, MLflow + pipeline services |

## Quick start

```bash
# Python 3.11+ and uv (https://docs.astral.sh/uv/)
uv sync
uv run pytest

# Local infrastructure
cp .env.example .env
docker compose -f deploy/compose/docker-compose.yml up -d
# Redpanda console: http://localhost:8081   MLflow: http://localhost:5000
```

```python
from botgraph_core import WindowSpec, build_window_graph, read_ctu13_binetflow, sliding_windows

flows = read_ctu13_binetflow("capture20110810.binetflow")
for window in sliding_windows(flows, WindowSpec(size_s=300, hop_s=60)):
    graph = build_window_graph(window.flows, window.window_id)
    print(window.window_id, graph.num_nodes, graph.num_edges)
```

## Live detection pipeline

```
flows.raw ──▶ ingest ──▶ flows.normalized ──▶ detector ──▶ detections / alerts ──▶ SQLite / Postgres
 (replay,     validate,                        5-min windows every minute,
  Zeek...)    bad rows → flows.dlq             graph → GATv2 → k-of-n alerts,
                                               learning mode, explanations
```

**Run the demo on your laptop** (no broker or Docker needed; in-process bus + SQLite):

```bash
uv run botgraph run --replay ctu13:6 --fresh     # Menti: a botnet family the model never saw
uv run botgraph run --replay iot23:CTU-IoT-Malware-Capture-34-1 --speed 600   # Mirai, 60-min learning
uv run botgraph alerts                           # alerts with their explanations
```

The replay streams a recorded capture with **labels stripped**; the pipeline never sees ground
truth. A live terminal view shows windows scored, warnings, alerts and recent events, and the
run ends with a summary scored against the hidden labels.

Demo on CTU-13 scenario 6 (Menti, a family the model never trained on), on a MacBook Air:

| Infected host | Normal hosts | Time to alert | Throughput | Window latency |
|---|---|---|---|---|
| alerted (1/1) | 0/6 alerted | 14 min | 22k flows/s, **306× real time** | p50 35 ms, p95 39 ms |

This matches the offline evaluation for Menti exactly. 51 other university hosts that CTU-13
leaves unlabelled were also alerted; with no ground truth for them, they cannot be scored.

- **Streaming windows** use event time with a watermark and allowed lateness; on in-order input
  they are identical to the batch windows used in training (randomised property tests).
- **Alerts** at two levels: *warning* (flagged in 3 of the last 5 windows) and *alert* (12 of
  15). Alert times match the offline evaluation exactly (tested).
- **Learning mode**: a new network is observed first (default 60 min); the threshold becomes
  the 95th percentile of its baseline scores, the calibration validated on IoT-23. Thresholds
  are stored, so a restart does not relearn; `botgraph recalibrate` starts over.
- **Explanations** (Integrated Gradients on the logit) are computed on the host's 2-hop neighbourhood, which gives
  exactly the model's score for a 2-layer GNN at a fraction of the cost.
- Quiet networks are handled: windows need only 1 flow (a lone beacon still gets scored).

**As separate services over Kafka/Redpanda + Postgres** (the same code; verified in CI against a
real broker):

```bash
docker compose -f deploy/compose/docker-compose.yml --profile pipeline up -d
docker compose -f deploy/compose/docker-compose.yml --profile pipeline run --rm replay \
    replay --replay ctu13:6 --speed 60
# or without Docker for the services themselves:
uv run botgraph ingest --metrics-port 9101
uv run botgraph detect --internal-nets 147.32.0.0/16 --learning-minutes 0 --metrics-port 9102
uv run botgraph replay --replay ctu13:6 --speed 60
```

Flows are keyed by sensor, so each network segment stays on one Kafka partition and detectors
scale out by partition. Prometheus metrics (`--metrics-port`): flows ingested/rejected, windows
scored, per-window latency, alert events.

## Data and model pipeline

Each step is a module CLI and a [DVC](https://dvc.org) stage (`dvc.yaml`, parameters in
`params.yaml`), so `dvc repro` reruns only the steps whose code, params or inputs changed.

```bash
uv run python -m botgraph_ml.download ctu13      # ~2 GB archive -> ml/data/raw/ctu13/scenario=N/
uv run python -m botgraph_ml.prepare ctu13       # -> typed Parquet flow tables
uv run python -m botgraph_ml.build_graphs ctu13  # -> window graphs (.npz) + labelled node tables
uv run python -m botgraph_ml.baseline            # -> ml/reports/baseline/metrics.json

# or all at once, with caching:
uv tool install dvc && dvc init && dvc repro baseline
```

### GNN models

```bash
uv run python -m botgraph_ml.gnn.train --model e_graphsage   # or graphsage | gatv2
uv run python -m botgraph_ml.gnn.hpo --model e_graphsage --trials 30
uv run python -m botgraph_ml.gnn.explain --model e_graphsage --window <window.npz> --top 3
uv run python -m botgraph_ml.compare                          # -> ml/reports/comparison.md

# Track runs in MLflow (from the compose stack):
MLFLOW_TRACKING_URI=http://localhost:5000 uv run python -m botgraph_ml.gnn.train
```

| Model | Message passing |
|---|---|
| GraphSAGE | Mean of neighbour embeddings; ignores flow statistics |
| E-GraphSAGE | Each message includes the flow edge's features (bytes, timing, periodicity, …) |
| **GATv2** | Attention over neighbours; edge features weight the attention (primary model) |

All three share one architecture (encoder, residual conv layers, MLP head), so comparisons
isolate the message-passing layer. Each flow is added in both directions with a direction flag,
so a host learns from traffic it sends as well as traffic it receives.

On a synthetic sanity check where bots differ **only** in a periodic flow to a shared C2 host
(node features are pure noise), validation PR-AUC was GraphSAGE 0.49, GATv2 0.60 and
E-GraphSAGE 1.00 (chance level 0.33): passing flow statistics inside messages matters when the
evidence lives only on edges. On real CTU-13 traffic, where host features also carry signal,
GATv2 generalised best to unseen families, so it is the primary model.

Cross-validation and alert tuning:

```bash
uv run python -m botgraph_ml.gnn.train --model gatv2 --split lofo:Menti --epochs 10 --patience 3
uv run python -m botgraph_ml.alert_tuning   # choose the alert rule on validation folds only
```

Explanations (Integrated Gradients on the host's logit) list the flows and host features that
pushed its score up, with raw
(unscaled) values an analyst can read.

Every report includes window-level precision, recall, F1, PR-AUC and FPR at 95% recall, plus
host-level alert outcomes (bots and normal hosts alerted, time to alert) per botnet family.

## Datasets

- **CTU-13** (Stratosphere Lab): 13 labelled botnet scenarios, used for training and evaluation.
  Splits hold out entire botnet families (`ml/labels/ctu13.yaml`).
- **IoT-23** (Stratosphere Lab): Zeek logs from IoT malware and benign IoT honeypots, used for
  the cross-dataset test (`python -m botgraph_ml.download iot23`, `prepare iot23`, then
  `python -m botgraph_ml.cross_dataset score|xgboost|report` and `python -m botgraph_ml.calibrate`).

## Roadmap

- [x] Phase 0: monorepo, tooling, CI, local infrastructure
- [~] Phase 1: dataset download + normalization ✅, DVC pipeline ✅, EDA
- [x] Phase 2: graph construction, XGBoost baseline, evaluation harness
- [x] Phase 3: GraphSAGE / E-GraphSAGE / GATv2, HPO, explainability, MLflow tracking,
      leave-one-family-out CV, alert-rule tuning
- [x] Phase 4: live pipeline: streaming windows, detector, two-level alerts, learning mode,
      explanations, replay, Kafka/Postgres services, Docker, metrics
- [ ] Phase 5: FastAPI + Next.js analyst console
- [ ] Phase 6: observability, drift monitoring, security hardening, Helm
- [ ] Phase 7: demo, write-up ([model card](docs/model_card.md) done)

## Known issues

- **macOS: never import XGBoost and torch in one process.** Their OpenMP runtimes deadlock.
  Pipeline stages run as separate processes, and the baseline test runs XGBoost in a child
  process for the same reason.

## Responsible use

Only capture traffic on networks you own or are authorised to monitor. The lab environment
simulates bot behaviour with scripts and never runs real malware.
