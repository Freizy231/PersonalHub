"""Migrations run only through an explicitly supplied maintenance connection."""
from alembic import context

connection = context.config.attributes.get("connection")
if connection is None:
    raise RuntimeError("Use python -m backend.manage; no implicit production connections")
context.configure(connection=connection, target_metadata=None, render_as_batch=True)
with context.begin_transaction():
    context.run_migrations()
