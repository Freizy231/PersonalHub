"""Private lightweight ledger and manually recorded exercise. Additive only."""
from alembic import op
revision='0011_finance_exercise'
down_revision='0010_diet'
branch_labels=None
depends_on=None


def upgrade():
    for sql in [
        """CREATE TABLE finance_categories(id TEXT PRIMARY KEY NOT NULL,name TEXT NOT NULL,
        direction TEXT NOT NULL CHECK(direction IN ('income','expense')),version INTEGER NOT NULL CHECK(version>0),
        created_at TEXT NOT NULL,updated_at TEXT NOT NULL,UNIQUE(name,direction))""",
        """CREATE TABLE finance_entries(id TEXT PRIMARY KEY NOT NULL,local_date TEXT NOT NULL,
        category_id TEXT NOT NULL REFERENCES finance_categories(id) ON DELETE RESTRICT,
        currency TEXT NOT NULL CHECK(currency IN ('CNY','USD','EUR','JPY')),
        amount_minor INTEGER NOT NULL CHECK(amount_minor BETWEEN 1 AND 1000000000000),memo TEXT NOT NULL,
        version INTEGER NOT NULL CHECK(version>0),created_at TEXT NOT NULL,updated_at TEXT NOT NULL,deleted_at TEXT)""",
        'CREATE INDEX ix_finance_month ON finance_entries(local_date,deleted_at,currency,category_id)',
        """CREATE TABLE finance_budgets(id TEXT PRIMARY KEY NOT NULL,month TEXT NOT NULL,
        category_id TEXT NOT NULL REFERENCES finance_categories(id) ON DELETE RESTRICT,
        currency TEXT NOT NULL CHECK(currency IN ('CNY','USD','EUR','JPY')),
        amount_minor INTEGER NOT NULL CHECK(amount_minor BETWEEN 0 AND 1000000000000),
        version INTEGER NOT NULL CHECK(version>0),created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
        UNIQUE(month,category_id,currency))""",
        """CREATE TABLE exercise_plans(id TEXT PRIMARY KEY NOT NULL,title TEXT NOT NULL,local_date TEXT NOT NULL,
        kind TEXT NOT NULL CHECK(kind IN ('run','walk','strength','cycle','other')),
        duration_seconds INTEGER NOT NULL CHECK(duration_seconds BETWEEN 0 AND 604800),
        distance_meters INTEGER NOT NULL CHECK(distance_meters BETWEEN 0 AND 1000000),
        task_id TEXT REFERENCES tasks(resource_id) ON DELETE RESTRICT,event_id TEXT REFERENCES events(resource_id) ON DELETE RESTRICT,
        cancelled INTEGER NOT NULL CHECK(cancelled IN (0,1)),version INTEGER NOT NULL CHECK(version>0),
        created_at TEXT NOT NULL,updated_at TEXT NOT NULL,deleted_at TEXT)""",
        'CREATE INDEX ix_exercise_plan_date ON exercise_plans(local_date,deleted_at,id)',
        """CREATE TABLE exercise_records(id TEXT PRIMARY KEY NOT NULL,local_date TEXT NOT NULL,
        kind TEXT NOT NULL CHECK(kind IN ('run','walk','strength','cycle','other')),
        duration_seconds INTEGER NOT NULL CHECK(duration_seconds BETWEEN 1 AND 604800),
        distance_meters INTEGER NOT NULL CHECK(distance_meters BETWEEN 0 AND 1000000),memo TEXT NOT NULL,
        plan_id TEXT REFERENCES exercise_plans(id) ON DELETE RESTRICT,version INTEGER NOT NULL CHECK(version>0),
        created_at TEXT NOT NULL,updated_at TEXT NOT NULL,deleted_at TEXT)""",
        'CREATE INDEX ix_exercise_record_date ON exercise_records(local_date,deleted_at,id)',
        'CREATE INDEX ix_exercise_record_plan ON exercise_records(plan_id,deleted_at)',
    ]:op.get_bind().exec_driver_sql(sql)


def downgrade():raise RuntimeError('Restore full snapshot into a new root with matching application')
