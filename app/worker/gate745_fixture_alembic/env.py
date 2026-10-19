"""Use only the already-admitted connection, never Settings/DSN/env fallback."""

from alembic import context
from sqlalchemy.engine import Connection

connection = context.config.attributes.get("connection")
if context.is_offline_mode() or not isinstance(connection, Connection):
    raise RuntimeError("E_FIXTURE_ADMITTED_CONNECTION_REQUIRED")
context.configure(connection=connection)
with context.begin_transaction():
    context.run_migrations()
