# Cross-dataset test: CTU-13 models on IoT-23 (no retraining)

21,994 labelled host-windows from 11 captures (11,109 bot, 10,885 benign). Thresholds are each model's CTU-13 validation threshold, unchanged.

| Model | PR-AUC | Precision | Recall | FPR @ 95% recall | Latency p95 (ms/window) |
|---|---|---|---|---|---|
| XGBoost | 0.476 | 0.438 | 0.357 | 0.993 | n/a |
| GraphSAGE | 0.974 | 0.722 | 0.997 | 0.334 | 0.9 |
| E-GraphSAGE | 0.944 | 0.632 | 0.966 | 0.517 | 1.0 |
| GATv2 | 0.959 | 0.634 | 0.962 | 0.486 | 1.7 |

## Alerts per device

| Model | Level | Rule | Infected devices alerted | Benign devices alerted | Median time to alert |
|---|---|---|---|---|---|
| XGBoost | warning | 3 of 5 | 9/9 | 16/22 | 3 min |
| XGBoost | alert | 12 of 15 | 6/9 | 5/22 | 13 min |
| GraphSAGE | warning | 3 of 5 | 9/9 | 15/22 | 2 min |
| GraphSAGE | alert | 12 of 15 | 8/9 | 5/22 | 11 min |
| E-GraphSAGE | warning | 3 of 5 | 9/9 | 15/22 | 4 min |
| E-GraphSAGE | alert | 12 of 15 | 9/9 | 5/22 | 21 min |
| GATv2 | warning | 3 of 5 | 9/9 | 13/22 | 4 min |
| GATv2 | alert | 12 of 15 | 8/9 | 4/22 | 53 min |

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
| CTU-IoT-Malware-Capture-8-1 | 1376 | 79 | 0.924 | 1.000 | 1.000 | 1.000 |
