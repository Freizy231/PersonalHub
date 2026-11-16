"""Ordering index for the intelligence list; no column or data change."""
from alembic import op

revision = '0013_intel_ordering'
down_revision = '0012_intelligence_assistant'
branch_labels = None
depends_on = None


def upgrade():
    # The list endpoint filters on title/summary and orders by updated_at DESC, id.
    # Without a matching index SQLite scans the whole table and sorts a temp B-tree.
    op.execute('CREATE INDEX IF NOT EXISTS ix_intel_ordering ON intel_items(updated_at DESC,id DESC)')


def downgrade():
    raise RuntimeError('Downgrade is not supported; restore a matching backup instead.')
