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

## Leave-one-family-out cross-validation

Each botnet family is the test set once; validation uses a different family.
Normal hosts are counted once per scenario (CTU-13 reuses the same 6 normal hosts).

| Model | Family | PR-AUC | FPR @ 95% recall | Bots alerted | Normal hosts alerted | Median time to alert |
|---|---|---|---|---|---|---|
| XGBoost | Menti | 0.993 | 0.010 | 1/1 | 5/6 | 2 min |
| XGBoost | Murlo | 0.650 | 0.156 | 1/1 | 6/6 | 2 min |
| XGBoost | NSIS.ay | 0.416 | 0.877 | 0/3 | 0/6 | n/a |
| XGBoost | Neris | 0.943 | 0.304 | 12/12 | 13/18 | 2 min |
| XGBoost | Rbot | 0.718 | 0.461 | 15/15 | 13/24 | 6 min |
| XGBoost | Sogou | 0.736 | 0.464 | 1/1 | 2/6 | 7 min |
| XGBoost | Virut | 0.792 | 0.058 | 2/2 | 1/12 | 8 min |
| E-GraphSAGE | Menti | 1.000 | 0.000 | 1/1 | 0/6 | 2 min |
| E-GraphSAGE | Murlo | 0.829 | 0.039 | 1/1 | 6/6 | 2 min |
| E-GraphSAGE | NSIS.ay | 0.297 | 0.818 | 0/3 | 0/6 | n/a |
| E-GraphSAGE | Neris | 0.971 | 0.106 | 12/12 | 4/18 | 2 min |
| E-GraphSAGE | Rbot | 0.843 | 0.920 | 15/15 | 9/24 | 2 min |
| E-GraphSAGE | Sogou | 0.968 | 0.026 | 1/1 | 0/6 | 2 min |
| E-GraphSAGE | Virut | 0.999 | 0.000 | 2/2 | 2/12 | 2 min |
| GATv2 | Menti | 0.997 | 0.007 | 1/1 | 1/6 | 9 min |
| GATv2 | Murlo | 0.964 | 0.016 | 1/1 | 5/6 | 2 min |
| GATv2 | NSIS.ay | 0.307 | 0.928 | 2/3 | 0/6 | 2 min |
| GATv2 | Neris | 0.969 | 0.099 | 12/12 | 5/18 | 2 min |
| GATv2 | Rbot | 0.901 | 0.146 | 15/15 | 11/24 | 2 min |
| GATv2 | Sogou | 0.952 | 0.190 | 1/1 | 0/6 | 2 min |
| GATv2 | Virut | 1.000 | 0.000 | 2/2 | 0/12 | 5 min |

### Summary across folds

| Model | Folds | Mean PR-AUC ± std | Bots alerted | Normal hosts alerted |
|---|---|---|---|---|
| XGBoost | 7 | 0.750 ± 0.192 | 32/35 | 40/78 |
| E-GraphSAGE | 7 | 0.844 ± 0.251 | 32/35 | 21/78 |
| GATv2 | 7 | 0.870 ± 0.250 | 34/35 | 22/78 |
