# Model card: BotGraph GATv2 host classifier

## Summary

A graph attention network (GATv2) that scores every host in a 5-minute network communication
graph for the probability that it is a botnet-infected machine. Scores feed a host alert
rule ("flagged in 12 of the last 15 minutes"). Trained and evaluated on CTU-13.

| | |
|---|---|
| Architecture | Encoder MLP → 2 × GATv2Conv (64 hidden, 4 heads, edge features in attention, residual + LayerNorm) → MLP head |
| Parameters | 24,065 |
| Inputs | 15 host features and 13 edge features per 5-minute window (see `packages/botgraph-core/src/botgraph_core/graph.py`) |
| Output | One logit per host per window; sigmoid gives a bot score in [0, 1] |
| Inference | ~9 ms mean, ~15 ms p95 per window on a laptop CPU (Apple silicon, 2 threads) |
| Training | AdamW, weighted BCE (positive weight = benign/bot ratio), early stopping on validation PR-AUC |
| Artifacts | `ml/models/gatv2/` (`model.pt` state dict, `config.json`, `scaler.json`) |

## Intended use

- **Intended:** flagging hosts on a monitored network for review by a security analyst, as one
  signal among others. Alerts come with GNNExplainer explanations (the flows and host features
  that drove the score).
- **Not intended:** automatic blocking or disciplinary decisions without human review; networks
  you are not authorised to monitor; detecting single malicious flows (the unit is a host over
  time, not a packet or connection).

## Data

**CTU-13** (Stratosphere Lab, CTU Prague, 2011): 13 captures from a university network with
real botnet infections, as Argus bidirectional flows. About 20 million flows in total; non-IP
flows (ARP, ~0.001%) are dropped.

- **Labels are host-level:** infected IPs and 6 verified normal hosts per scenario
  (`ml/labels/ctu13.yaml`, cross-checked against the dataset's own flow labels). All other
  hosts are *unknown*: they stay in the graph as context but are excluded from the loss.
- **Windows:** 5-minute windows every minute; 7,962 windows with at least 10 flows.
- **Families:** Neris (3 scenarios), Rbot (4), Virut (2), Menti, Sogou, Murlo, NSIS.ay (1 each).

## Evaluation

**Protocol:** leave-one-family-out cross-validation. Each family is the test set once; a
different family is the validation set (early stopping, decision threshold); the remaining
five train the model. The alert rule is chosen once, on the pooled validation folds, then
applied unchanged to the test folds (`python -m botgraph_ml.alert_tuning`).

| Model | Mean PR-AUC ± std | Bots alerted | Normal hosts falsely alerted |
|---|---|---|---|
| XGBoost (host features only) | 0.750 ± 0.19 | 30/35 | 11/78 |
| E-GraphSAGE | 0.844 ± 0.25 | 29/35 | 10/78 |
| **GATv2** | **0.870 ± 0.25** | **30/35** | **5/78** |

Per family (GATv2, PR-AUC): Menti 0.997, Murlo 0.964, NSIS.ay 0.307, Neris 0.969,
Rbot 0.901, Sogou 0.952, Virut 1.000. Full tables: `ml/reports/comparison.md`.

Normal hosts are counted once per scenario, so 78 = 6 hosts × 13 scenarios.

## Limitations

- **Small normal population.** CTU-13 labels only 6 normal hosts, reused in every scenario.
  False-alert rates are estimated from those hosts and will not transfer directly to a larger,
  more varied network. Treat 5/78 as an early indication, not a guarantee.
- **Peer-to-peer botnets.** NSIS.ay (the only P2P family) is poorly detected by every model
  (PR-AUC 0.30–0.42): with no central C2 server, its traffic resembles nothing in training.
- **Short-lived infections.** The high-confidence rule needs 12 flagged minutes out of 15. A bot
  active for only a few minutes (e.g. Sogou's 26-minute capture) can end before it fires; the
  planned *warning* level (3 of 5 minutes) catches 34/35 bots but with 22/78 false alerts.
- **Time to alert.** Median ~28 minutes under the high-confidence rule.
- **Dataset age.** CTU-13 dates from 2011. Modern botnets use encrypted, CDN-fronted or DoH C2
  channels; the flow features here do not inspect payloads, but the behaviour mix has shifted.
- **Recurring IP.** The main bot IP (147.32.84.165) recurs across scenarios. Features do not
  include IP addresses, but the same machine's baseline behaviour appears in train and test.

## Next validation steps

1. Cross-dataset test on **IoT-23** (different network, device types and malware).
2. Traffic from a controlled lab with simulated C2 beacons and realistic benign load.
3. False-alert rate per host-day on a larger benign population before any production use.

## Responsible use

Only deploy on networks you own or are authorised to monitor. Alerts are leads for human
investigation, not verdicts.
