# Alert rule tuning

Rule chosen on validation families only: >= 90% of validation bots alerted, then fewest normal-host alerts, then fastest alert.
The chosen rule is then applied unchanged to the test families.

| Model | Rule | Test bots alerted | Test normal hosts alerted | Median time to alert |
|---|---|---|---|---|
| XGBoost (default) | 3 of 5 | 32/35 | 40/78 | 2 min |
| XGBoost (tuned) | 15 of 15 | 20/35 | 6/78 | 154 min |
| E-GraphSAGE (default) | 3 of 5 | 32/35 | 21/78 | 2 min |
| E-GraphSAGE (tuned) | 12 of 15 | 29/35 | 10/78 | 29 min |
| GATv2 (default) | 3 of 5 | 34/35 | 22/78 | 2 min |
| GATv2 (tuned) | 12 of 15 | 30/35 | 5/78 | 28 min |
