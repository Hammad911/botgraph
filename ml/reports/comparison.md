# Model comparison (held-out botnet families)

Window level: one row per (host, 5-minute window).
Threshold chosen on validation families only.

| Model | Precision | Recall | F1 | PR-AUC | FPR @ 95% recall | Latency p95 (ms/window) |
|---|---|---|---|---|---|---|
| xgboost_node_features | 0.786 | 0.305 | 0.439 | 0.473 | 0.687 | n/a |
| graphsage | 0.885 | 0.312 | 0.461 | 0.691 | 0.617 | 9.1 |
| e_graphsage | 0.908 | 0.378 | 0.534 | 0.806 | 0.449 | 9.6 |
| gatv2 | 0.920 | 0.362 | 0.520 | 0.878 | 0.106 | 10.8 |

## Per family (host level)

| Model | Family | Bots detected | Host F1 | Median time to detect |
|---|---|---|---|---|
| xgboost_node_features | Menti | 1/1 | 1.000 | 0 min |
| xgboost_node_features | Sogou | 1/1 | 0.000 | 2 min |
| xgboost_node_features | Murlo | 1/1 | 0.000 | 0 min |
| xgboost_node_features | NSIS.ay | 3/3 | 0.000 | 0 min |
| graphsage | Menti | 1/1 | 1.000 | 1 min |
| graphsage | Sogou | 1/1 | 0.000 | 2 min |
| graphsage | Murlo | 1/1 | 0.000 | 2 min |
| graphsage | NSIS.ay | 3/3 | 0.000 | 3 min |
| e_graphsage | Menti | 1/1 | 1.000 | 0 min |
| e_graphsage | Sogou | 1/1 | 0.000 | 0 min |
| e_graphsage | Murlo | 1/1 | 0.000 | 0 min |
| e_graphsage | NSIS.ay | 3/3 | 0.000 | 2 min |
| gatv2 | Menti | 1/1 | 1.000 | 1 min |
| gatv2 | Sogou | 1/1 | 0.000 | 0 min |
| gatv2 | Murlo | 1/1 | 0.000 | 0 min |
| gatv2 | NSIS.ay | 3/3 | 0.000 | 1 min |
