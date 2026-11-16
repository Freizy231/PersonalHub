"""Personal projects reuse shared tasks and immutable notes."""
from alembic import op
revision = '0008_projects'
down_revision = '0007_phase2'
branch_labels = None
depends_on = None


def upgrade():
    c = op.get_bind()
    if c.exec_driver_sql('PRAGMA foreign_keys').scalar():
        raise RuntimeError('Use verified maintenance upgrade')
    if c.exec_driver_sql("SELECT 1 FROM sqlite_schema WHERE type='trigger' AND tbl_name='resources'").first():
        raise RuntimeError('Unrecognized resource triggers')
    indexes = c.exec_driver_sql("SELECT name,sql FROM sqlite_schema WHERE type='index' AND tbl_name='resources' AND sql IS NOT NULL").fetchall()
    with op.batch_alter_table('resources', recreate='always') as b:
        b.drop_constraint('resource_kind', type_='check')
        b.create_check_constraint('resource_kind', "kind IN ('task','event','note','collection','document','book','bookmark','course','learning_goal','learning_path','knowledge_node','project')")
    # Reflection can normalize quoting/spacing or skip expression indexes. Recreate
    # the original definitions verbatim; maintenance verifies every schema object.
    for name, sql in indexes:
        c.exec_driver_sql('DROP INDEX IF EXISTS "'+name.replace('"','""')+'"')
        c.exec_driver_sql(sql)
    for sql in [
        """CREATE TABLE projects (resource_id TEXT PRIMARY KEY NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
            description TEXT NOT NULL DEFAULT '', objective TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'planned' CHECK(status IN ('planned','active','paused','completed','archived')),
            local_directory TEXT NOT NULL DEFAULT '', repository_url TEXT NOT NULL DEFAULT '',
            start_date TEXT, target_date TEXT, last_opened_at TEXT,
            CHECK(start_date IS NULL OR target_date IS NULL OR target_date>=start_date))""",
        'CREATE INDEX ix_projects_status ON projects(status,resource_id)',
        'CREATE INDEX ix_projects_recent ON projects(last_opened_at,resource_id)',
        """CREATE TABLE project_milestones (id TEXT PRIMARY KEY NOT NULL,
            project_id TEXT NOT NULL REFERENCES projects(resource_id) ON DELETE RESTRICT,
            title TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', target_date TEXT,
            status TEXT NOT NULL DEFAULT 'todo' CHECK(status IN ('todo','doing','done')),
            position INTEGER NOT NULL DEFAULT 0 CHECK(position>=0),
            version INTEGER NOT NULL DEFAULT 1 CHECK(version>0),
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT,
            UNIQUE(id,project_id))""",
        'CREATE INDEX ix_project_milestones ON project_milestones(project_id,deleted_at,position,id)',
        """CREATE TABLE project_tasks (id TEXT PRIMARY KEY NOT NULL,
            project_id TEXT NOT NULL REFERENCES projects(resource_id) ON DELETE RESTRICT,
            task_id TEXT NOT NULL UNIQUE REFERENCES tasks(resource_id) ON DELETE RESTRICT,
            milestone_id TEXT, created_at TEXT NOT NULL,
            FOREIGN KEY(milestone_id,project_id) REFERENCES project_milestones(id,project_id) ON DELETE RESTRICT)""",
        'CREATE INDEX ix_project_tasks ON project_tasks(project_id,milestone_id,task_id)',
        """CREATE TABLE project_entries (id TEXT PRIMARY KEY NOT NULL,
            project_id TEXT NOT NULL REFERENCES projects(resource_id) ON DELETE RESTRICT,
            note_id TEXT NOT NULL REFERENCES notes(resource_id) ON DELETE RESTRICT,
            entry_kind TEXT NOT NULL CHECK(entry_kind IN ('log','experiment','summary')),
            created_at TEXT NOT NULL, UNIQUE(project_id,note_id))""",
        'CREATE INDEX ix_project_entries ON project_entries(project_id,entry_kind,created_at,id)',
        'CREATE INDEX ix_project_entries_note ON project_entries(note_id,project_id)',
    ]:
        c.exec_driver_sql(sql)


def downgrade():
    raise RuntimeError('Restore verified full snapshot and matching application')
