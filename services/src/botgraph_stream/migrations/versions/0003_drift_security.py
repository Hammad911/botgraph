"""Drift baselines and reports, audit log, account lockout and token revocation (Phase 6)."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "drift_baselines",
        sa.Column("sensor_id", sa.String(128), primary_key=True),
        sa.Column("reference", sa.JSON, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
    )
    op.create_table(
        "drift_reports",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("sensor_id", sa.String(128), nullable=False),
        sa.Column("window_start", sa.Float, nullable=False),
        sa.Column("baseline_psi", sa.Float, nullable=True),
        sa.Column("training_psi", sa.Float, nullable=True),
        sa.Column("report", sa.JSON, nullable=False),
    )
    op.create_index("ix_drift_reports_sensor", "drift_reports", ["sensor_id", "window_start"])
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("at", sa.DateTime, nullable=False),
        sa.Column("actor", sa.String(64), nullable=True),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("target", sa.String(256), nullable=True),
        sa.Column("client", sa.String(64), nullable=True),
        sa.Column("detail", sa.JSON, nullable=True),
    )
    op.create_index("ix_audit_events_at", "audit_events", ["at"])
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("failed_logins", sa.Integer, nullable=False, server_default="0"))
        batch.add_column(sa.Column("locked_until", sa.DateTime, nullable=True))
        batch.add_column(sa.Column("token_version", sa.Integer, nullable=False, server_default="0"))
        batch.add_column(
            sa.Column("disabled", sa.Boolean, nullable=False, server_default=sa.false())
        )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        for column in ("disabled", "token_version", "locked_until", "failed_logins"):
            batch.drop_column(column)
    op.drop_table("audit_events")
    op.drop_table("drift_reports")
    op.drop_table("drift_baselines")
