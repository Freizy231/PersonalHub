"""Events, explicit task association and persistent one-shot inbox reminders."""
from alembic import op
import sqlalchemy as sa

revision = '0002_events_reminders'
down_revision = '0001_tasks'
branch_labels = None
depends_on = None


def upgrade():
    con = op.get_bind()
    # Maintenance disables FK enforcement BEFORE the explicit transaction, then
    # checks all FKs and every original table fingerprint before committing.
    if con.exec_driver_sql('PRAGMA foreign_keys').scalar():
        raise RuntimeError('Use verified maintenance upgrade; FK-safe table copy required')
    if con.exec_driver_sql("SELECT 1 FROM sqlite_schema WHERE type='trigger' AND tbl_name='resources'").first():
        raise RuntimeError('Unrecognized resource triggers; preserve and review manually')
    with op.batch_alter_table('resources', recreate='always') as batch:
        batch.drop_constraint('resource_kind', type_='check')
        batch.create_check_constraint('resource_kind', "kind IN ('task','event')")
    op.create_table('events',
        sa.Column('resource_id', sa.Text, sa.ForeignKey('resources.id', ondelete='CASCADE'), primary_key=True, nullable=False),
        sa.Column('description', sa.Text), sa.Column('is_all_day', sa.Integer, nullable=False),
        sa.Column('start_at', sa.Text), sa.Column('end_at', sa.Text),
        sa.Column('start_date', sa.Text), sa.Column('end_date', sa.Text),
        sa.Column('timezone', sa.Text, nullable=False), sa.Column('location', sa.Text),
        sa.Column('status', sa.Text, nullable=False, server_default='scheduled'),
        sa.Column('task_id', sa.Text, sa.ForeignKey('tasks.resource_id', ondelete='RESTRICT')),
        sa.CheckConstraint("status IN ('scheduled','completed','cancelled')", name='event_status'),
        sa.CheckConstraint("(is_all_day=0 AND start_at IS NOT NULL AND end_at>start_at AND start_date IS NULL AND end_date IS NULL) OR (is_all_day=1 AND start_date IS NOT NULL AND end_date>start_date AND start_at IS NULL AND end_at IS NULL)", name='event_interval'),
        sa.CheckConstraint('(is_all_day=0 AND end_at IS NOT NULL) OR (is_all_day=1 AND end_date IS NOT NULL)', name='event_end'))
    for name,columns in [('ix_events_time',['start_at','end_at']),('ix_events_dates',['start_date','end_date']),
                         ('ix_events_status',['status','start_at']),('ix_events_task',['task_id'])]:
        op.create_index(name,'events',columns)
    op.create_table('reminders',
        sa.Column('id',sa.Text,primary_key=True,nullable=False),
        sa.Column('resource_id',sa.Text,sa.ForeignKey('resources.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('remind_at',sa.Text,nullable=False),sa.Column('status',sa.Text,nullable=False,server_default='pending'),
        sa.Column('delivered_at',sa.Text),sa.Column('acknowledged_at',sa.Text),
        sa.Column('delivery_key',sa.Text,nullable=False,unique=True),
        sa.Column('created_at',sa.Text,nullable=False),sa.Column('updated_at',sa.Text,nullable=False),
        sa.Column('version',sa.Integer,nullable=False,server_default='1'),
        sa.CheckConstraint("status IN ('pending','delivered','missed','cancelled')",name='reminder_status'),
        sa.CheckConstraint("(status='delivered' AND delivered_at IS NOT NULL) OR (status!='delivered' AND delivered_at IS NULL)",name='reminder_delivery'),
        sa.CheckConstraint('version>0',name='reminder_version'))
    op.create_index('ix_reminders_due','reminders',['status','remind_at'])
    op.create_index('ix_reminders_resource','reminders',['resource_id','status'])


def downgrade():
    raise RuntimeError('Restore a verified backup and matching application, never destructive downgrade')
