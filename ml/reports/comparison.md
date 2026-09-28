# Model comparison

## Main split (test families: Menti, Sogou, Murlo, NSIS.ay)

Window level: one row per (host, 5-minute window). Threshold chosen on validation only.

| Model | Precision | Recall | F1 | PR-AUC | FPR @ 95% recall | Latency p95 (ms/window) |
|---|---|---|---|---|---|---|
| XGBoost | 0.786 | 0.305 | 0.439 | 0.473 | 0.687 | n/a |
| GraphSAGE | 0.885 | 0.312 | 0.461 | 0.691 | 0.617 | 3.6 |
| E-GraphSAGE | 0.908 | 0.378 | 0.534 | 0.806 | 0.449 | 5.1 |
| GATv2 | 0.920 | 0.362 | 0.520 | 0.878 | 0.106 | 10.0 |

### Alerts per family (a host alerts when flagged in 3 of the last 5 windows)

| Model | Family | Bots alerted | Normal hosts alerted | Median time to alert |
|---|---|---|---|---|
| XGBoost | Menti | 1/1 | 2/6 | 2 min |
| XGBoost | Sogou | 1/1 | 0/6 | 4 min |
| XGBoost | Murlo | 1/1 | 4/6 | 2 min |
| XGBoost | NSIS.ay | 2/3 | 2/6 | 3 min |
| GraphSAGE | Menti | 1/1 | 0/6 | 3 min |
| GraphSAGE | Sogou | 1/1 | 0/6 | 4 min |
| GraphSAGE | Murlo | 1/1 | 3/6 | 4 min |
| GraphSAGE | NSIS.ay | 3/3 | 0/6 | 5 min |
| E-GraphSAGE | Menti | 1/1 | 0/6 | 2 min |
| E-GraphSAGE | Sogou | 1/1 | 0/6 | 2 min |
| E-GraphSAGE | Murlo | 1/1 | 3/6 | 2 min |
| E-GraphSAGE | NSIS.ay | 3/3 | 0/6 | 4 min |
| GATv2 | Menti | 1/1 | 0/6 | 3 min |
| GATv2 | Sogou | 1/1 | 0/6 | 2 min |
| GATv2 | Murlo | 1/1 | 3/6 | 2 min |
| GATv2 | NSIS.ay | 3/3 | 0/6 | 3 min |
