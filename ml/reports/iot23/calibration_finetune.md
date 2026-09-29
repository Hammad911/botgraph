# IoT calibration of the CTU-13 GATv2 (finetune)

Fine-tuned on 2 benign IoT honeypot captures (+ CTU-13 training windows); tested on the held-out honeypot and all IoT-23 malware captures.
Before = uncalibrated CTU-13 model.

## Held-out benign IoT devices

| Held out | Benign windows flagged (before → after) | Devices alerted, 12 of 15 (before → after) | Devices alerted, 3 of 5 (before → after) |
|---|---|---|---|
| Honeypot 4-1 | 50% → 77% | 1/1 → 1/1 | 1/1 → 1/1 |
| Honeypot 5-1 | 91% → 64% | 2/10 → 2/10 | 9/10 → 10/10 |
| Honeypot 7-1_Somfy-01 | 31% → 48% | 0/3 → 1/3 | 1/3 → 1/3 |

## Malware captures and CTU-13 (did it forget bots?)

| Held out | IoT window PR-AUC | Infected devices alerted (12 of 15) | Benign devices alerted (12 of 15) | CTU-13 test PR-AUC |
|---|---|---|---|---|
| Honeypot 4-1 | 0.964 → 0.923 | 8/9 → 9/9 | 1/8 → 1/8 | 0.878 → 0.826 |
| Honeypot 5-1 | 0.964 → 0.961 | 8/9 → 9/9 | 1/8 → 1/8 | 0.878 → 0.857 |
| Honeypot 7-1_Somfy-01 | 0.964 → 0.893 | 8/9 → 9/9 | 1/8 → 1/8 | 0.878 → 0.822 |
