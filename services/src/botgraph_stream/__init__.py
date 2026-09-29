"""BotGraph live pipeline: ingest, streaming windows, GNN detection, alerting and replay."""

import logging
import warnings

# Emitted by torch_geometric on import; not actionable for users of the CLI.
warnings.filterwarnings(
    "ignore", message="`torch.jit.script` is deprecated", category=FutureWarning
)

# Library convention: silent unless the application configures logging (botgraph_stream.logs).
# Without this, WARNING-level alert logs would print over `botgraph run`'s live view.
logging.getLogger("botgraph").addHandler(logging.NullHandler())
