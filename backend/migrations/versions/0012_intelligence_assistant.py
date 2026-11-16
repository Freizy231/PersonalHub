"""Bounded multi-source intelligence and optional user-configured API assistant."""
from alembic import op
revision='0012_intelligence_assistant'
down_revision='0011_finance_exercise'
branch_labels=None
depends_on=None

def upgrade():
    for sql in [
        """CREATE TABLE intel_sources(id TEXT PRIMARY KEY NOT NULL,kind TEXT NOT NULL
        CHECK(kind IN ('rss','hn','arxiv','huggingface')),name TEXT NOT NULL,endpoint TEXT NOT NULL,
        enabled INTEGER NOT NULL CHECK(enabled IN(0,1)),version INTEGER NOT NULL CHECK(version>0),created_at TEXT NOT NULL,updated_at TEXT NOT NULL)""",
        """CREATE TABLE intel_runs(id TEXT PRIMARY KEY NOT NULL,source_id TEXT REFERENCES intel_sources(id) ON DELETE RESTRICT,
        status TEXT NOT NULL CHECK(status IN ('running','success','partial','failed','interrupted')),started_at TEXT NOT NULL,finished_at TEXT,
        payload_text TEXT,received INTEGER NOT NULL DEFAULT 0,saved INTEGER NOT NULL DEFAULT 0,error_code TEXT,operation_key TEXT NOT NULL UNIQUE)""",
        """CREATE TABLE intel_items(id TEXT PRIMARY KEY NOT NULL,source_key TEXT NOT NULL,remote_id TEXT NOT NULL,
        title TEXT NOT NULL,url TEXT NOT NULL,target_key TEXT NOT NULL,summary TEXT NOT NULL,domain TEXT NOT NULL DEFAULT '',
        favorite INTEGER NOT NULL DEFAULT 0 CHECK(favorite IN(0,1)),read_state TEXT NOT NULL DEFAULT 'new' CHECK(read_state IN('new','later','read')),
        version INTEGER NOT NULL CHECK(version>0),created_at TEXT NOT NULL,updated_at TEXT NOT NULL,UNIQUE(source_key,remote_id))""",
        'CREATE INDEX ix_intel_target ON intel_items(target_key)',
        'CREATE INDEX ix_intel_source ON intel_items(source_key,updated_at,id)',
        """CREATE TABLE intel_snapshots(id TEXT PRIMARY KEY NOT NULL,item_id TEXT NOT NULL REFERENCES intel_items(id) ON DELETE RESTRICT,
        run_id TEXT REFERENCES intel_runs(id) ON DELETE RESTRICT,captured_at TEXT NOT NULL,raw_json TEXT NOT NULL,
        metrics_json TEXT NOT NULL,raw_available INTEGER NOT NULL CHECK(raw_available IN(0,1)),dedup_key TEXT UNIQUE)""",
        'CREATE INDEX ix_intel_snapshot_time ON intel_snapshots(item_id,captured_at,id)',
        """CREATE TABLE intel_subscriptions(id TEXT PRIMARY KEY NOT NULL,keyword TEXT NOT NULL UNIQUE,
        enabled INTEGER NOT NULL CHECK(enabled IN(0,1)),version INTEGER NOT NULL CHECK(version>0),created_at TEXT NOT NULL,updated_at TEXT NOT NULL)""",
        """CREATE TABLE intel_legacy_batches(id TEXT PRIMARY KEY NOT NULL,digest TEXT NOT NULL UNIQUE,created_at TEXT NOT NULL,counts_json TEXT NOT NULL)""",
        """CREATE TABLE intel_legacy_rows(batch_id TEXT NOT NULL REFERENCES intel_legacy_batches(id) ON DELETE RESTRICT,
        table_name TEXT NOT NULL,row_id TEXT NOT NULL,stable_id TEXT NOT NULL,row_json TEXT NOT NULL,
        PRIMARY KEY(batch_id,table_name,row_id))""",
        """CREATE TABLE assistant_config(id INTEGER PRIMARY KEY CHECK(id=1),endpoint TEXT NOT NULL,model TEXT NOT NULL,
        enabled INTEGER NOT NULL CHECK(enabled IN(0,1)),credential_ref TEXT,version INTEGER NOT NULL CHECK(version>0),updated_at TEXT NOT NULL)""",
        """CREATE TABLE assistant_runs(id TEXT PRIMARY KEY NOT NULL,operation_key TEXT NOT NULL UNIQUE,
        owner_hash TEXT NOT NULL,preview_id TEXT NOT NULL UNIQUE,status TEXT NOT NULL
        CHECK(status IN ('starting','success','failed','cancelled','unknown')),created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,error_code TEXT)""",
    ]:op.get_bind().exec_driver_sql(sql)

def downgrade():raise RuntimeError('Restore verified full snapshot and matching application')
