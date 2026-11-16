"""One-shot, version-bound project tool launches; no published DDL changes."""
from alembic import op
revision='0009_project_tools'
down_revision='0008_projects'
branch_labels=None
depends_on=None

def upgrade():
    c=op.get_bind()
    for sql in [
        """CREATE TABLE project_tool_profiles (
            id TEXT PRIMARY KEY NOT NULL, name TEXT NOT NULL,
            adapter TEXT NOT NULL UNIQUE CHECK(adapter IN ('terminal','vscode','codex')),
            installation_id TEXT NOT NULL, identity_json TEXT NOT NULL,
            enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
            version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
        """CREATE TABLE project_tool_previews (
            id TEXT PRIMARY KEY NOT NULL,
            project_id TEXT NOT NULL REFERENCES projects(resource_id) ON DELETE RESTRICT,
            profile_id TEXT NOT NULL REFERENCES project_tool_profiles(id) ON DELETE RESTRICT,
            project_version INTEGER NOT NULL CHECK(project_version>0),
            profile_version INTEGER NOT NULL CHECK(profile_version>0),
            plan_json TEXT NOT NULL, expires_at TEXT NOT NULL,
            created_at TEXT NOT NULL, confirmed_at TEXT)""",
        'CREATE INDEX ix_project_tool_previews ON project_tool_previews(project_id,created_at,id)',
        """CREATE TABLE project_tool_history (
            id TEXT PRIMARY KEY NOT NULL,
            preview_id TEXT NOT NULL UNIQUE REFERENCES project_tool_previews(id) ON DELETE RESTRICT,
            project_id TEXT NOT NULL REFERENCES projects(resource_id) ON DELETE RESTRICT,
            profile_id TEXT NOT NULL REFERENCES project_tool_profiles(id) ON DELETE RESTRICT,
            status TEXT NOT NULL CHECK(status IN ('starting','started','failed','uncertain')),
            error_code TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
        'CREATE INDEX ix_project_tool_history ON project_tool_history(project_id,created_at,id)',
    ]:c.exec_driver_sql(sql)

def downgrade():
    raise RuntimeError('Restore verified full snapshot and matching application')
