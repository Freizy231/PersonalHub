"""Minimal ordinary task loop; no future business tables, no legacy database."""
from alembic import op
import sqlalchemy as sa

revision = '0001_tasks'
down_revision = '0000_foundation'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('resources',
        sa.Column('id', sa.Text, primary_key=True, nullable=False),
        sa.Column('kind', sa.Text, nullable=False),
        sa.Column('title', sa.Text, nullable=False), sa.Column('summary', sa.Text),
        sa.Column('privacy_level', sa.Text, nullable=False, server_default='personal'),
        sa.Column('created_at', sa.Text, nullable=False), sa.Column('updated_at', sa.Text, nullable=False),
        sa.Column('deleted_at', sa.Text), sa.Column('version', sa.Integer, nullable=False, server_default='1'),
        sa.CheckConstraint("kind IN ('task')", name='resource_kind'),
        sa.CheckConstraint("privacy_level IN ('personal','private')", name='resource_privacy'),
        sa.CheckConstraint('length(trim(title)) BETWEEN 1 AND 500', name='resource_title'),
        sa.CheckConstraint('version > 0', name='resource_version'))
    for name, columns in [('ix_resources_kind_updated', ['kind','deleted_at','updated_at','id']),
                          ('ix_resources_privacy_updated', ['privacy_level','deleted_at','updated_at','id'])]:
        op.create_index(name, 'resources', columns)
    op.create_table('tasks',
        sa.Column('resource_id', sa.Text, sa.ForeignKey('resources.id', ondelete='CASCADE'), primary_key=True, nullable=False),
        sa.Column('description', sa.Text), sa.Column('status', sa.Text, nullable=False, server_default='todo'),
        sa.Column('priority', sa.Integer, nullable=False, server_default='2'),
        sa.Column('planned_date', sa.Text), sa.Column('due_date', sa.Text), sa.Column('due_at', sa.Text),
        sa.Column('timezone', sa.Text, nullable=False), sa.Column('completed_at', sa.Text),
        sa.Column('estimated_seconds', sa.Integer), sa.Column('position', sa.Integer, nullable=False, server_default='0'),
        sa.CheckConstraint("status IN ('todo','doing','done','cancelled')", name='task_status'),
        sa.CheckConstraint('priority BETWEEN 0 AND 3 AND typeof(priority)=\'integer\'', name='task_priority'),
        sa.CheckConstraint('due_date IS NULL OR due_at IS NULL', name='task_deadline'),
        sa.CheckConstraint("(status='done' AND completed_at IS NOT NULL) OR (status!='done' AND completed_at IS NULL)", name='task_completed'),
        sa.CheckConstraint('position >= 0', name='task_position'),
        sa.CheckConstraint('estimated_seconds IS NULL OR estimated_seconds >= 0', name='task_estimate'))
    op.create_index('ix_tasks_plan', 'tasks', ['status','planned_date','priority','position'])
    op.create_index('ix_tasks_due_date', 'tasks', ['status','due_date'])
    op.create_index('ix_tasks_due_at', 'tasks', ['status','due_at'])
    op.create_table('activities',
        sa.Column('id', sa.Text, primary_key=True, nullable=False),
        sa.Column('resource_id', sa.Text, sa.ForeignKey('resources.id', ondelete='SET NULL')),
        sa.Column('action', sa.Text, nullable=False), sa.Column('actor', sa.Text, nullable=False),
        sa.Column('occurred_at', sa.Text, nullable=False), sa.Column('privacy_level', sa.Text, nullable=False),
        sa.Column('payload_json', sa.Text, nullable=False, server_default='{}'),
        sa.CheckConstraint("actor IN ('user','system','ai')", name='activity_actor'),
        sa.CheckConstraint("privacy_level IN ('personal','private')", name='activity_privacy'))
    for name, columns in [('ix_activity_time',['occurred_at','id']), ('ix_activity_resource',['resource_id','occurred_at']),
                          ('ix_activity_privacy',['privacy_level','occurred_at'])]:
        op.create_index(name,'activities',columns)
    op.create_table('operation_requests',
        sa.Column('id', sa.Text, primary_key=True, nullable=False),
        sa.Column('idempotency_key', sa.Text, nullable=False, unique=True),
        sa.Column('request_hash', sa.Text, nullable=False), sa.Column('operation_kind', sa.Text, nullable=False),
        sa.Column('status', sa.Text, nullable=False), sa.Column('result_json', sa.Text), sa.Column('expires_at', sa.Text),
        sa.Column('created_at', sa.Text, nullable=False), sa.Column('updated_at', sa.Text, nullable=False))
    op.create_index('ix_operation_expiry','operation_requests',['expires_at'])


def downgrade():
    raise RuntimeError('Restore a verified backup with its matching application version')
