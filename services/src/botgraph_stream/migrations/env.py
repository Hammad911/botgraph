"""Alembic environment. Run migrations through ``botgraph_stream.store.migrate(engine)``,
which passes an open connection in ``config.attributes["connection"]``."""

from alembic import context

from botgraph_stream.store import Base

connection = context.config.attributes.get("connection")
if connection is None:
    raise RuntimeError("run migrations via botgraph_stream.store.migrate(engine)")

context.configure(
    connection=connection,
    target_metadata=Base.metadata,
    render_as_batch=True,  # SQLite needs batch mode for ALTER TABLE
)
with context.begin_transaction():
    context.run_migrations()
