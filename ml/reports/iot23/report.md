# Cross-dataset test: CTU-13 models on IoT-23 (no retraining)

24,391 labelled host-windows from 14 captures (12,295 bot, 12,096 benign). Thresholds are each model's CTU-13 validation threshold, unchanged.

| Model | PR-AUC | Precision | Recall | FPR @ 95% recall | Latency p95 (ms/window) |
|---|---|---|---|---|---|
| XGBoost | 0.470 | 0.446 | 0.386 | 0.993 | n/a |
| GraphSAGE | 0.974 | 0.719 | 0.994 | 0.325 | 4.5 |
| E-GraphSAGE | 0.931 | 0.642 | 0.967 | 0.496 | 7.7 |
| GATv2 | 0.962 | 0.644 | 0.964 | 0.468 | 14.7 |

## Alerts per device

| Model | Level | Rule | Infected devices alerted | Benign devices alerted | Median time to alert |
|---|---|---|---|---|---|
| XGBoost | warning | 3 of 5 | 13/13 | 17/24 | 3 min |
| XGBoost | alert | 12 of 15 | 9/13 | 6/24 | 13 min |
| GraphSAGE | warning | 3 of 5 | 12/13 | 16/24 | 2 min |
| GraphSAGE | alert | 12 of 15 | 11/13 | 6/24 | 11 min |
| E-GraphSAGE | warning | 3 of 5 | 12/13 | 16/24 | 4 min |
| E-GraphSAGE | alert | 12 of 15 | 12/13 | 6/24 | 19 min |
| GATv2 | warning | 3 of 5 | 12/13 | 14/24 | 4 min |
| GATv2 | alert | 12 of 15 | 11/13 | 5/24 | 26 min |

## Per capture (window-level PR-AUC)

| Capture | Bot windows | Benign windows | XGBoost | GraphSAGE | E-GraphSAGE | GATv2 |
|---|---|---|---|---|---|---|
| CTU-Honeypot-Capture-4-1 | 0 | 734 | n/a | n/a | n/a | n/a |
| CTU-Honeypot-Capture-5-1 | 0 | 521 | n/a | n/a | n/a | n/a |
| CTU-Honeypot-Capture-7-1_Somfy-01 | 0 | 29 | n/a | n/a | n/a | n/a |
| CTU-IoT-Malware-Capture-1-1 | 6719 | 5481 | 0.451 | 1.000 | 1.000 | 1.000 |
| CTU-IoT-Malware-Capture-20-1 | 14 | 1435 | 0.751 | 0.006 | 0.010 | 0.006 |
| CTU-IoT-Malware-Capture-21-1 | 18 | 1308 | 0.785 | 0.009 | 0.010 | 0.008 |
| CTU-IoT-Malware-Capture-3-1 | 2157 | 45 | 0.983 | 1.000 | 0.991 | 0.999 |
| CTU-IoT-Malware-Capture-34-1 | 806 | 633 | 0.877 | 0.994 | 0.995 | 0.994 |
| CTU-IoT-Malware-Capture-42-1 | 6 | 503 | 0.059 | 0.008 | 0.008 | 0.021 |
| CTU-IoT-Malware-Capture-44-1 | 13 | 117 | 0.808 | 0.274 | 0.436 | 0.440 |
| CTU-IoT-Malware-Capture-48-1 | 591 | 53 | 0.911 | 1.000 | 1.000 | 1.000 |
| CTU-IoT-Malware-Capture-49-1 | 450 | 10 | 0.971 | 1.000 | 1.000 | 1.000 |
| CTU-IoT-Malware-Capture-60-1 | 145 | 1148 | 0.296 | 0.492 | 0.621 | 0.735 |
| CTU-IoT-Malware-Capture-8-1 | 1376 | 79 | 0.924 | 1.000 | 1.000 | 1.000 |
