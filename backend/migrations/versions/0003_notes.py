"""Ordinary Markdown notes, immutable revisions and durable file intents."""
from alembic import op
import sqlalchemy as sa
revision='0003_notes'
down_revision='0002_events_reminders'
branch_labels=None
depends_on=None


def upgrade():
    con=op.get_bind()
    if con.exec_driver_sql('PRAGMA foreign_keys').scalar():
        raise RuntimeError('Use verified maintenance upgrade')
    if con.exec_driver_sql("SELECT 1 FROM sqlite_schema WHERE type='trigger' AND tbl_name='resources'").first():
        raise RuntimeError('Unrecognized resource triggers')
    with op.batch_alter_table('resources',recreate='always') as batch:
        batch.drop_constraint('resource_kind',type_='check')
        batch.create_check_constraint('resource_kind',"kind IN ('task','event','note')")
    op.create_table('notes',
        sa.Column('resource_id',sa.Text,sa.ForeignKey('resources.id',ondelete='CASCADE'),primary_key=True,nullable=False),
        sa.Column('note_kind',sa.Text,nullable=False),sa.Column('local_date',sa.Text),
        sa.Column('body_path',sa.Text,nullable=False,unique=True),sa.Column('content_sha256',sa.Text,nullable=False),
        sa.Column('format',sa.Text,nullable=False,server_default='markdown'),
        sa.CheckConstraint("note_kind IN ('quick','journal','knowledge','experiment')",name='note_kind'),
        sa.CheckConstraint("format='markdown'",name='note_format'))
    op.create_index('ix_notes_kind_date','notes',['note_kind','local_date','resource_id'])
    op.create_table('note_revisions',
        sa.Column('id',sa.Text,primary_key=True,nullable=False),
        sa.Column('note_id',sa.Text,sa.ForeignKey('notes.resource_id',ondelete='CASCADE'),nullable=False),
        sa.Column('version',sa.Integer,nullable=False),sa.Column('body_path',sa.Text,nullable=False,unique=True),
        sa.Column('content_sha256',sa.Text,nullable=False),sa.Column('title',sa.Text,nullable=False),
        sa.Column('note_kind',sa.Text,nullable=False),sa.Column('local_date',sa.Text),sa.Column('created_at',sa.Text,nullable=False),
        sa.UniqueConstraint('note_id','version',name='note_revision_version'),
        sa.CheckConstraint('version>0',name='revision_positive'))
    op.create_index('ix_note_revisions_history','note_revisions',['note_id','created_at','id'])
    op.create_table('file_operations',
        sa.Column('id',sa.Text,primary_key=True,nullable=False),sa.Column('resource_id',sa.Text,nullable=False),
        sa.Column('operation_type',sa.Text,nullable=False),sa.Column('temp_path',sa.Text,nullable=False),
        sa.Column('final_path',sa.Text,nullable=False,unique=True),sa.Column('new_hash',sa.Text,nullable=False),
        sa.Column('state',sa.Text,nullable=False),sa.Column('created_at',sa.Text,nullable=False),sa.Column('updated_at',sa.Text,nullable=False),
        sa.CheckConstraint("state IN ('prepared','committed','orphaned','conflict')",name='file_operation_state'),
        sa.CheckConstraint("operation_type='note_revision'",name='file_operation_type'))
    op.create_index('ix_file_operations_state','file_operations',['state','updated_at'])


def downgrade():
    raise RuntimeError('Restore verified backup and matching application; no destructive downgrade')
