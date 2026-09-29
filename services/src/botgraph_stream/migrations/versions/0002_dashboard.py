"""Dashboard: alert triage fields, host score history, graph snapshots, users (Phase 5)."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("alerts") as batch:
        batch.add_column(sa.Column("status", sa.String(16), nullable=False, server_default="open"))
        batch.add_column(sa.Column("assignee", sa.String(64), nullable=True))
        batch.add_column(sa.Column("note", sa.String(2000), nullable=True))
        batch.add_column(sa.Column("updated_at", sa.DateTime, nullable=True))
    op.create_table(
        "host_scores",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("sensor_id", sa.String(128), nullable=False),
        sa.Column("ip", sa.String(64), nullable=False),
        sa.Column("window_start", sa.Float, nullable=False),
        sa.Column("score", sa.Float, nullable=False),
    )
    op.create_index("ix_host_scores_host", "host_scores", ["sensor_id", "ip", "window_start"])
    op.create_index("ix_host_scores_window", "host_scores", ["sensor_id", "window_start"])
    op.create_table(
        "graph_snapshots",
        sa.Column("sensor_id", sa.String(128), primary_key=True),
        sa.Column("window_id", sa.String(64), nullable=False),
        sa.Column("window_start", sa.Float, nullable=False),
        sa.Column("graph", sa.JSON, nullable=False),
        sa.Column("updated_at", sa.DateTime, nullable=False),
    )
    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("username", sa.String(64), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(256), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )


def downgrade() -> None:
    op.drop_table("users")
    op.drop_table("graph_snapshots")
    op.drop_table("host_scores")
    with op.batch_alter_table("alerts") as batch:
        for column in ("updated_at", "note", "assignee", "status"):
            batch.drop_column(column)
