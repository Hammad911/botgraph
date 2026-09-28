"""BotGraph core: flow schema, dataset adapters, windowing and graph features."""

from botgraph_core.adapters import (
    iter_ctu13_binetflow,
    iter_zeek_conn,
    read_ctu13_binetflow,
    read_zeek_conn,
)
from botgraph_core.graph import (
    EDGE_FEATURES,
    NODE_FEATURES,
    GraphConfig,
    WindowGraph,
    build_window_graph,
)
from botgraph_core.schema import FLOW_COLUMNS, FlowRecord, Label, Proto, validate_frame
from botgraph_core.windowing import Window, WindowSpec, sliding_windows

__all__ = [
    "EDGE_FEATURES",
    "FLOW_COLUMNS",
    "NODE_FEATURES",
    "FlowRecord",
    "GraphConfig",
    "Label",
    "Proto",
    "Window",
    "WindowGraph",
    "WindowSpec",
    "build_window_graph",
    "iter_ctu13_binetflow",
    "iter_zeek_conn",
    "read_ctu13_binetflow",
    "read_zeek_conn",
    "sliding_windows",
    "validate_frame",
]
