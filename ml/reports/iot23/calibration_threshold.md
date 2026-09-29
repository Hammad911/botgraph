# IoT calibration of the CTU-13 GATv2 (threshold)

Alert threshold raised to the 95% quantile of 2 benign IoT honeypot captures' scores (model unchanged); tested on the held-out honeypot and all IoT-23 malware captures.
Before = uncalibrated CTU-13 model.

## Held-out benign IoT devices

| Held out | Benign windows flagged (before → after) | Devices alerted, 12 of 15 (before → after) | Devices alerted, 3 of 5 (before → after) |
|---|---|---|---|
| Honeypot 4-1 | 50% → 2% | 1/1 → 0/1 | 1/1 → 1/1 |
| Honeypot 5-1 | 91% → 26% | 2/10 → 1/10 | 9/10 → 7/10 |
| Honeypot 7-1_Somfy-01 | 31% → 0% | 0/3 → 0/3 | 1/3 → 0/3 |

## Malware captures and CTU-13 (did it forget bots?)

| Held out | IoT window PR-AUC | Infected devices alerted (12 of 15) | Benign devices alerted (12 of 15) | CTU-13 test PR-AUC |
|---|---|---|---|---|
| Honeypot 4-1 | 0.966 → 0.966 | 11/13 → 6/13 | 2/10 → 0/10 | unchanged (same model) |
| Honeypot 5-1 | 0.966 → 0.966 | 11/13 → 10/13 | 2/10 → 0/10 | unchanged (same model) |
| Honeypot 7-1_Somfy-01 | 0.966 → 0.966 | 11/13 → 8/13 | 2/10 → 0/10 | unchanged (same model) |

## Infected device alerted after calibration (12 of 15), per held-out fold

| Capture | 4-1 | 5-1 | 7-1_Somfy-01 |
|---|---|---|---|
| 1-1 | yes | yes | yes |
| 20-1 | no | yes | no |
| 21-1 | no | yes | no |
| 3-1 | yes | yes | yes |
| 34-1 | yes | yes | yes |
| 42-1 | no | yes | yes |
| 44-1 | no | no | no |
| 48-1 | yes | yes | yes |
| 49-1 | yes | yes | yes |
| 60-1 | yes | yes | yes |
| 8-1 | no | yes | yes |
