"""Initial pipeline schema: sensors, alerts, window_stats (Phase 4)."""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sensors",
        sa.Column("id", sa.String(128), primary_key=True),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("threshold", sa.Float, nullable=True),
        sa.Column("model_threshold", sa.Float, nullable=True),
        sa.Column("baseline_scores", sa.Integer, nullable=False),
        sa.Column("calibrated_at", sa.DateTime, nullable=True),
        sa.Column("last_window_start", sa.Float, nullable=True),
    )
    op.create_table(
        "alerts",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("sensor_id", sa.String(128), nullable=False),
        sa.Column("ip", sa.String(64), nullable=False),
        sa.Column("level", sa.String(16), nullable=False),
        sa.Column("window_start", sa.Float, nullable=False),
        sa.Column("score", sa.Float, nullable=False),
        sa.Column("hits", sa.Integer, nullable=False),
        sa.Column("rule_k", sa.Integer, nullable=False),
        sa.Column("rule_n", sa.Integer, nullable=False),
        sa.Column("threshold", sa.Float, nullable=False),
        sa.Column("explanation", sa.JSON, nullable=True),
        sa.Column("raised_at", sa.DateTime, nullable=False),
        sa.Column("cleared_window_start", sa.Float, nullable=True),
    )
    op.create_index("ix_alerts_sensor_id", "alerts", ["sensor_id"])
    op.create_index("ix_alerts_ip", "alerts", ["ip"])
    op.create_table(
        "window_stats",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("sensor_id", sa.String(128), nullable=False),
        sa.Column("window_id", sa.String(64), nullable=False),
        sa.Column("window_start", sa.Float, nullable=False),
        sa.Column("n_flows", sa.Integer, nullable=False),
        sa.Column("n_nodes", sa.Integer, nullable=False),
        sa.Column("n_edges", sa.Integer, nullable=False),
        sa.Column("n_hosts", sa.Integer, nullable=False),
        sa.Column("max_score", sa.Float, nullable=False),
        sa.Column("latency_ms", sa.Float, nullable=False),
    )
    op.create_index("ix_window_stats_sensor_id", "window_stats", ["sensor_id"])
    op.create_index("ix_window_stats_window_start", "window_stats", ["window_start"])


def downgrade() -> None:
    op.drop_table("window_stats")
    op.drop_table("alerts")
    op.drop_table("sensors")
