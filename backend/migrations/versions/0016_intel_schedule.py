"""Per-source query, collection schedule and the translated-summary column.

The upgrade audit compares every pre-existing table's column list and row digests before
and after a migration, so an existing table can never gain a column. The new facts
therefore live in new, additive tables keyed by the existing ids, and `intel_sources` /
`intel_items` are not touched at all:

* `intel_source_settings` — what to pull for a source (`query`), and the schedule slot it
  last ran for (`last_scheduled_at`). A source with no row uses the adapter's default list.
* `intel_item_text` — the unambiguous summary in the other language (`summary_zh`). An item
  with no row simply has no second language yet.
* `intel_schedule` — a single row, disabled by default.

Every new table starts empty, so nothing existing changes value.
"""
from alembic import op

revision = '0016_intel_schedule'
down_revision = '0015_meal_dishes'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE intel_source_settings (
        source_id TEXT PRIMARY KEY NOT NULL REFERENCES intel_sources(id) ON DELETE CASCADE,
        query TEXT NOT NULL DEFAULT '' CHECK(length(query)<=200),
        last_scheduled_at TEXT,
        updated_at TEXT NOT NULL)""")

    op.execute("""CREATE TABLE intel_item_text (
        item_id TEXT PRIMARY KEY NOT NULL REFERENCES intel_items(id) ON DELETE CASCADE,
        summary_zh TEXT NOT NULL DEFAULT '' CHECK(length(summary_zh)<=20000),
        source_hash TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL)""")

    # "this source, newest first" for the per-source card view, without a temp sort.
    op.execute('CREATE INDEX IF NOT EXISTS ix_intel_source_recent ON intel_items(source_key,updated_at DESC,id DESC)')

    # One row, like assistant_config. `enabled` is deliberately 0: nothing reaches the
    # network on a timer until the person turns it on.
    op.execute("""CREATE TABLE intel_schedule (
        id INTEGER PRIMARY KEY CHECK(id=1),
        enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN(0,1)),
        runs_per_day INTEGER NOT NULL DEFAULT 2 CHECK(runs_per_day BETWEEN 1 AND 24),
        local_hours TEXT NOT NULL DEFAULT '09:00,21:00',
        last_run_at TEXT, next_run_at TEXT,
        version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),
        updated_at TEXT NOT NULL)""")


def downgrade():
    raise RuntimeError('Downgrade is not supported; restore a matching backup instead.')
