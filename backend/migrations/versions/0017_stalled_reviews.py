"""A record of what was decided about something that had gone quiet.

The upgrade audit compares every pre-existing table's column list and row digests before
and after a migration, so no existing table can gain a column. A review therefore lives in
its own table, keyed by the resource it concerns, and `resources` / `activities` are not
touched.

Why a separate table rather than a status column: a review must not rewrite history. It is a
new fact ("on this date I looked at this and decided to keep / reschedule / drop it"), and the
original `updated_at` must stay exactly what it was. If reviews were folded into
`resources.updated_at`, the staleness view would lose its whole point — everything you ever
reviewed would look freshly touched, and you could no longer tell a thing that is genuinely
moving from one you merely triaged.

`decisions` is per-kind so a person can answer honestly without the app inventing a meaning:
'done' finishes it, 'continue' keeps it (optionally to a date), 'snooze' says "ask me later"
without pretending it moved, 'drop' sets it aside. Anything 'continue'-d with a date is
carried by the ordinary per-kind field (task status/planned_date, project target/ status,
learning node mastery), so no scheduling logic is duplicated here.
"""
from alembic import op

revision = '0017_stalled_reviews'
down_revision = '0016_intel_schedule'
branch_labels = None
depends_on = None

DECISIONS = ('done', 'continue', 'snooze', 'drop')


def upgrade():
    op.execute(f"""CREATE TABLE stalled_reviews (
        id TEXT PRIMARY KEY NOT NULL,
        resource_id TEXT NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
        decision TEXT NOT NULL CHECK(decision IN ({','.join(repr(d) for d in DECISIONS)})),
        review_date TEXT NOT NULL,
        note TEXT NOT NULL DEFAULT '' CHECK(length(note)<=2000),
        next_date TEXT,
        created_at TEXT NOT NULL)""")

    # "what was last decided about this resource" and "what did I decide on a given day"
    # both have to be index-backed: the widget asks the first on every dashboard load.
    op.execute('CREATE INDEX ix_stalled_review_resource ON stalled_reviews(resource_id,created_at DESC)')
    op.execute('CREATE INDEX ix_stalled_review_when ON stalled_reviews(review_date,id DESC)')


def downgrade():
    raise RuntimeError('Downgrade is not supported; restore a matching backup instead.')
