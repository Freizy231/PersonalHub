"""Ordering index plus an EPUB-capable blob media type; no rows change."""
from alembic import op

revision = '0014_epub_documents'
down_revision = '0013_intel_ordering'
branch_labels = None
depends_on = None


def upgrade():
    op.execute('CREATE INDEX IF NOT EXISTS ix_intel_ordering ON intel_items(updated_at DESC,id DESC)')
    # blobs.media_type was constrained to application/pdf. SQLite cannot relax a
    # CHECK constraint in place, so rebuild the table with identical columns.
    # Row values, order and every other table stay byte-identical, which the
    # upgrade audit verifies.
    op.execute("""CREATE TABLE blobs_new (
        id TEXT PRIMARY KEY NOT NULL,
        sha256 TEXT NOT NULL UNIQUE CHECK(length(sha256)=64),
        size INTEGER NOT NULL CHECK(size>0 AND size<=104857600),
        relative_path TEXT NOT NULL UNIQUE,
        media_type TEXT NOT NULL CHECK(media_type IN ('application/pdf','application/epub+zip')),
        created_at TEXT NOT NULL)""")
    op.execute("INSERT INTO blobs_new(id,sha256,size,relative_path,media_type,created_at) "
               "SELECT id,sha256,size,relative_path,media_type,created_at FROM blobs ORDER BY id")
    op.execute('DROP TABLE blobs')
    op.execute('ALTER TABLE blobs_new RENAME TO blobs')


def downgrade():
    raise RuntimeError('Downgrade is not supported; restore a matching backup instead.')
