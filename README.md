# BotGraph

**Botnet detection with graph neural networks.** BotGraph turns network flows into a
host-to-host communication graph every minute and uses a GNN trained in-house to flag infected
hosts. It targets the coordinated behaviour that per-flow detectors miss: C2 beaconing,
fan-out scanning and peer-to-peer bot meshes.

> Status: **Phases 0–2 done.** Feature pipeline, dataset pipeline and XGBoost baseline are
> implemented and tested. GNN models, streaming services and the web console come next.

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
- [ ] Phase 3: GraphSAGE / E-GraphSAGE / GATv2, HPO, explainability, model registry
- [ ] Phase 4: streaming pipeline (ingest → graph-builder → inference) + replay tool
- [ ] Phase 5: FastAPI + Next.js analyst console
- [ ] Phase 6: observability, drift monitoring, security hardening, Helm
- [ ] Phase 7: model card, demo, write-up

## Responsible use

Only capture traffic on networks you own or are authorised to monitor. The lab environment
simulates bot behaviour with scripts and never runs real malware.
