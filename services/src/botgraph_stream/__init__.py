"""BotGraph live pipeline: ingest, streaming windows, GNN detection, alerting and replay."""

import warnings

# Emitted by torch_geometric on import; not actionable for users of the CLI.
warnings.filterwarnings(
    "ignore", message="`torch.jit.script` is deprecated", category=FutureWarning
)
