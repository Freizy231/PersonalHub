"""AI-native workspace facts: authorization, conversations, plans, runs and templates.

The upgrade audit compares every pre-existing table's column list and row digests before and
after a migration, so no existing table gains a column. Everything new therefore lives in new
tables keyed by existing ids, and `resources`/`activities`/`assistant_*` are untouched.

The split follows the shape of the facts:

* `ai_grants` — what the person has allowed, and until when. Two independent rows kinds:
  local writes inside a scope, and sending content to a named endpoint+model.
* `ai_conversations` / `ai_messages` — a goal-scoped conversation. Message bodies are files
  (the note protocol), not blobs in SQLite, so this only holds ordering and references.
* `ai_plans` / `ai_plan_steps` — a candidate plan with its steps, frozen once adopted so a
  later model response cannot silently reinterpret work already done.
* `ai_runs` / `ai_run_steps` — one execution of a plan or template, with per-step status,
  idempotency key and result reference.
* `ai_templates` — a saved, versioned combination of registered capabilities.
* `ai_preferences` — the few preferences the person confirmed; nothing inferred.

Every table starts empty, so nothing existing changes value.
"""
from alembic import op

revision = '0018_ai_workspace'
down_revision = '0017_stalled_reviews'
branch_labels = None
depends_on = None

GRANT_KINDS = ('local_write', 'outbound')
RUN_STATUS = ('queued', 'running', 'waiting', 'success', 'partial', 'failed', 'cancelled', 'verify')
STEP_STATUS = ('pending', 'running', 'done', 'failed', 'skipped', 'verify')


def upgrade():
    # ------------------------------------------------------------------ authorization
    op.execute(f"""CREATE TABLE ai_grants (
        id TEXT PRIMARY KEY NOT NULL,
        kind TEXT NOT NULL CHECK(kind IN ({','.join(repr(k) for k in GRANT_KINDS)})),
        scope_id TEXT,
        scope_kind TEXT NOT NULL DEFAULT '' CHECK(length(scope_kind)<=40),
        endpoint TEXT NOT NULL DEFAULT '',
        model TEXT NOT NULL DEFAULT '',
        config_version INTEGER NOT NULL DEFAULT 0,
        enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN(0,1)),
        expires_at TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(kind, scope_id))""")
    op.execute('CREATE INDEX ix_ai_grants_kind ON ai_grants(kind,enabled)')

    # ------------------------------------------------------------------ conversation
    op.execute("""CREATE TABLE ai_conversations (
        id TEXT PRIMARY KEY NOT NULL,
        scope_id TEXT,
        scope_kind TEXT NOT NULL DEFAULT 'learning_goal',
        title TEXT NOT NULL DEFAULT '' CHECK(length(title)<=500),
        state TEXT NOT NULL DEFAULT 'active' CHECK(state IN ('active','archived')),
        summary_text TEXT NOT NULL DEFAULT '' CHECK(length(summary_text)<=20000),
        summary_upto INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL)""")
    op.execute('CREATE INDEX ix_ai_conversations_scope ON ai_conversations(scope_id,created_at DESC)')

    # Message bodies are managed files; this table only orders them and records provenance.
    op.execute("""CREATE TABLE ai_messages (
        id TEXT PRIMARY KEY NOT NULL,
        conversation_id TEXT NOT NULL REFERENCES ai_conversations(id) ON DELETE CASCADE,
        seq INTEGER NOT NULL CHECK(seq>=0),
        role TEXT NOT NULL CHECK(role IN ('user','assistant','system')),
        body_path TEXT NOT NULL DEFAULT '' CHECK(length(body_path)<=1024),
        body_sha256 TEXT NOT NULL DEFAULT '',
        source_ids_json TEXT NOT NULL DEFAULT '[]' CHECK(length(source_ids_json)<=20000),
        origin TEXT NOT NULL DEFAULT 'user' CHECK(origin IN ('user','model','derived')),
        created_at TEXT NOT NULL,
        UNIQUE(conversation_id,seq))""")

    # ------------------------------------------------------------------ plans
    op.execute("""CREATE TABLE ai_plans (
        id TEXT PRIMARY KEY NOT NULL,
        conversation_id TEXT REFERENCES ai_conversations(id) ON DELETE SET NULL,
        scope_id TEXT,
        title TEXT NOT NULL CHECK(length(title)<=500),
        rationale TEXT NOT NULL DEFAULT '' CHECK(length(rationale)<=20000),
        state TEXT NOT NULL DEFAULT 'draft' CHECK(state IN ('draft','adopted','discarded')),
        model TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        adopted_at TEXT)""")
    op.execute('CREATE INDEX ix_ai_plans_conversation ON ai_plans(conversation_id,created_at DESC)')

    # A step names a registered capability and carries validated JSON arguments.
    op.execute("""CREATE TABLE ai_plan_steps (
        id TEXT PRIMARY KEY NOT NULL,
        plan_id TEXT NOT NULL REFERENCES ai_plans(id) ON DELETE CASCADE,
        position INTEGER NOT NULL CHECK(position>=0),
        capability_id TEXT NOT NULL CHECK(length(capability_id)<=80),
        capability_version INTEGER NOT NULL CHECK(capability_version>0),
        arguments_json TEXT NOT NULL CHECK(length(arguments_json)<=20000),
        state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','done','failed','skipped')),
        result_ref TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        UNIQUE(plan_id,position))""")

    # ------------------------------------------------------------------ runs
    op.execute(f"""CREATE TABLE ai_runs (
        id TEXT PRIMARY KEY NOT NULL,
        operation_key TEXT NOT NULL UNIQUE,
        owner_hash TEXT NOT NULL,
        trigger TEXT NOT NULL DEFAULT 'manual' CHECK(trigger IN ('manual','schedule','retry')),
        plan_id TEXT REFERENCES ai_plans(id) ON DELETE SET NULL,
        template_id TEXT,
        grant_id TEXT,
        status TEXT NOT NULL CHECK(status IN ({','.join(repr(s) for s in RUN_STATUS)})),
        error_code TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL)""")
    op.execute('CREATE INDEX ix_ai_runs_status ON ai_runs(status,updated_at)')

    op.execute(f"""CREATE TABLE ai_run_steps (
        id TEXT PRIMARY KEY NOT NULL,
        run_id TEXT NOT NULL REFERENCES ai_runs(id) ON DELETE CASCADE,
        position INTEGER NOT NULL CHECK(position>=0),
        capability_id TEXT NOT NULL,
        capability_version INTEGER NOT NULL,
        arguments_json TEXT NOT NULL DEFAULT '{{}}',
        idempotency_key TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ({','.join(repr(s) for s in STEP_STATUS)})),
        result_summary TEXT NOT NULL DEFAULT '' CHECK(length(result_summary)<=4000),
        resource_ids_json TEXT NOT NULL DEFAULT '[]',
        error_code TEXT,
        started_at TEXT,
        finished_at TEXT,
        UNIQUE(run_id,position))""")

    # ------------------------------------------------------------------ templates
    op.execute("""CREATE TABLE ai_templates (
        id TEXT PRIMARY KEY NOT NULL,
        name TEXT NOT NULL CHECK(length(name)<=200),
        purpose TEXT NOT NULL DEFAULT '' CHECK(length(purpose)<=1000),
        version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),
        enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN(0,1)),
        steps_json TEXT NOT NULL CHECK(length(steps_json)<=40000),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL)""")
    op.execute('CREATE INDEX ix_ai_templates_enabled ON ai_templates(enabled,created_at DESC)')

    # ------------------------------------------------------------------ preferences
    op.execute("""CREATE TABLE ai_preferences (
        id INTEGER PRIMARY KEY CHECK(id=1),
        daily_minutes INTEGER NOT NULL DEFAULT 40 CHECK(daily_minutes BETWEEN 5 AND 600),
        session_minutes INTEGER NOT NULL DEFAULT 25 CHECK(session_minutes BETWEEN 5 AND 240),
        review_frequency TEXT NOT NULL DEFAULT 'weekly'
            CHECK(review_frequency IN ('off','daily','weekly')),
        reminder_hour INTEGER NOT NULL DEFAULT 9 CHECK(reminder_hour BETWEEN 0 AND 23),
        template_id TEXT,
        last_reviewed_at TEXT,
        version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),
        updated_at TEXT NOT NULL)""")

    # Derived content records the version and hash of each source it was built from, so a
    # later edit marks the content stale instead of silently treating it as current.
    op.execute("""CREATE TABLE ai_source_refs (
        id TEXT PRIMARY KEY NOT NULL,
        owner_table TEXT NOT NULL CHECK(owner_table IN ('message','plan')),
        owner_id TEXT NOT NULL,
        resource_id TEXT NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
        resource_version INTEGER NOT NULL CHECK(resource_version>0),
        source_hash TEXT NOT NULL CHECK(length(source_hash)=64),
        created_at TEXT NOT NULL,
        UNIQUE(owner_table,owner_id,resource_id))""")
    op.execute('CREATE INDEX ix_ai_source_refs_resource ON ai_source_refs(resource_id)')

    # Which scheduled reviews already produced a run, so a slot runs once.
    op.execute("""CREATE TABLE ai_review_slots (
        slot TEXT PRIMARY KEY NOT NULL,
        run_id TEXT REFERENCES ai_runs(id) ON DELETE SET NULL,
        created_at TEXT NOT NULL)""")


def downgrade():
    raise RuntimeError('Downgrade is not supported; restore a matching backup instead.')
