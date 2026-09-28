# BotGraph

**Botnet detection with graph neural networks.** BotGraph turns network flows into a
host-to-host communication graph every minute and uses a GNN trained in-house to flag infected
hosts. It targets the coordinated behaviour that per-flow detectors miss: C2 beaconing,
fan-out scanning and peer-to-peer bot meshes.

> Status: **Phases 0–3 done.** Feature pipeline, dataset pipeline, XGBoost baseline and three
> GNNs (with explanations and HPO) are implemented and tested. Streaming services and the web
> console come next.

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
| `services/` | ingest, graph-builder, inference, API *(planned)* |
| `web/` | Next.js analyst console *(planned)* |
| `deploy/compose` | Local infrastructure: Redpanda, Postgres, ClickHouse, Redis, MLflow |

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
| **E-GraphSAGE** | Each message includes the flow edge's features (bytes, timing, periodicity, …) |
| GATv2 | Attention over neighbours; edge features only weight the attention |

All three share one architecture (encoder, residual conv layers, MLP head), so comparisons
isolate the message-passing layer. Each flow is added in both directions with a direction flag,
so a host learns from traffic it sends as well as traffic it receives.

On a synthetic sanity check where bots differ **only** in a periodic flow to a shared C2 host
(node features are pure noise), validation PR-AUC was GraphSAGE 0.49, GATv2 0.60 and
E-GraphSAGE 1.00 (chance level 0.33). This is why E-GraphSAGE is the primary model.

Explanations (GNNExplainer) list the flows and host features that drove a detection, with raw
(unscaled) values an analyst can read.

The baseline report includes window- and host-level precision, recall, F1, PR-AUC, FPR at 95%
recall and time-to-detect, broken down per held-out botnet family. The decision threshold is
chosen on the validation families only.

## Datasets

- **CTU-13** (Stratosphere Lab): 13 labelled botnet scenarios, used for training and evaluation.
  Splits hold out entire botnet families (`ml/labels/ctu13.yaml`).
- **IoT-23** (Stratosphere Lab): Zeek logs from IoT malware, used for the cross-dataset test.

## Roadmap

- [x] Phase 0: monorepo, tooling, CI, local infrastructure
- [~] Phase 1: dataset download + normalization ✅, DVC pipeline ✅, EDA
- [x] Phase 2: graph construction, XGBoost baseline, evaluation harness
- [x] Phase 3: GraphSAGE / E-GraphSAGE / GATv2, HPO, explainability, MLflow tracking
- [ ] Phase 4: streaming pipeline (ingest → graph-builder → inference) + replay tool
- [ ] Phase 5: FastAPI + Next.js analyst console
- [ ] Phase 6: observability, drift monitoring, security hardening, Helm
- [ ] Phase 7: model card, demo, write-up

## Known issues

- **macOS: never import XGBoost and torch in one process.** Their OpenMP runtimes deadlock.
  Pipeline stages run as separate processes, and the baseline test runs XGBoost in a child
  process for the same reason.

## Responsible use

Only capture traffic on networks you own or are authorised to monitor. The lab environment
simulates bot behaviour with scripts and never runs real malware.
