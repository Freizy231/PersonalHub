"""Collections, typed relations and rebuildable ordinary full-text projection."""
from alembic import op
revision='0006_knowledge_foundation'
down_revision='0005_ordinary_tags'
branch_labels=None
depends_on=None

def upgrade():
    con=op.get_bind()
    if con.exec_driver_sql('PRAGMA foreign_keys').scalar():raise RuntimeError('Use verified maintenance upgrade')
    if con.exec_driver_sql("SELECT 1 FROM sqlite_schema WHERE type='trigger' AND tbl_name='resources'").first():raise RuntimeError('Unrecognized resource triggers')
    with op.batch_alter_table('resources',recreate='always') as batch:
        batch.drop_constraint('resource_kind',type_='check')
        batch.create_check_constraint('resource_kind',"kind IN ('task','event','note','collection')")
    for sql in [
        '''CREATE TABLE collections (resource_id TEXT PRIMARY KEY NOT NULL REFERENCES resources(id) ON DELETE CASCADE, description TEXT NOT NULL DEFAULT '')''',
        '''CREATE TABLE collection_members (collection_id TEXT NOT NULL REFERENCES collections(resource_id) ON DELETE CASCADE, resource_id TEXT NOT NULL REFERENCES resources(id) ON DELETE RESTRICT, position INTEGER NOT NULL CHECK(position>=0), created_at TEXT NOT NULL, PRIMARY KEY(collection_id,resource_id), CHECK(collection_id<>resource_id))''',
        'CREATE INDEX ix_collection_members_resource ON collection_members(resource_id,collection_id)',
        '''CREATE TABLE resource_relations (id TEXT PRIMARY KEY NOT NULL, source_id TEXT NOT NULL REFERENCES resources(id) ON DELETE RESTRICT, target_id TEXT NOT NULL REFERENCES resources(id) ON DELETE RESTRICT, relation_type TEXT NOT NULL CHECK(relation_type IN ('related','material','output')), created_at TEXT NOT NULL, UNIQUE(source_id,target_id,relation_type), CHECK(source_id<>target_id))''',
        'CREATE INDEX ix_relations_target ON resource_relations(target_id,relation_type,source_id)',
        '''CREATE TABLE note_links (source_id TEXT NOT NULL REFERENCES notes(resource_id) ON DELETE CASCADE, target_id TEXT NOT NULL REFERENCES resources(id) ON DELETE CASCADE, source_hash TEXT NOT NULL, created_at TEXT NOT NULL, PRIMARY KEY(source_id,target_id))''',
        'CREATE INDEX ix_note_links_target ON note_links(target_id,source_id)',
        '''CREATE TABLE search_entries (resource_id TEXT PRIMARY KEY NOT NULL REFERENCES resources(id) ON DELETE CASCADE, resource_version INTEGER NOT NULL, source_hash TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL, indexed_at TEXT NOT NULL)''',
        "CREATE VIRTUAL TABLE search_fts USING fts5(resource_id UNINDEXED,title,body,tokenize='trigram')",
    ]:con.exec_driver_sql(sql)

def downgrade():raise RuntimeError('Restore verified snapshot and matching application')
