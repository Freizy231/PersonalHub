"""Single-user dashboard layout and ordinary shortcut preferences."""
from alembic import op
import sqlalchemy as sa

revision = '0004_dashboard_preferences'
down_revision = '0003_notes'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('dashboard_preferences',
        sa.Column('id', sa.Text, primary_key=True),
        sa.Column('schema_version', sa.Integer, nullable=False),
        sa.Column('widgets_json', sa.Text, nullable=False),
        sa.Column('shortcuts_json', sa.Text, nullable=False),
        sa.Column('version', sa.Integer, nullable=False),
        sa.Column('created_at', sa.Text, nullable=False),
        sa.Column('updated_at', sa.Text, nullable=False),
        sa.CheckConstraint("id='dashboard'", name='dashboard_singleton'),
        sa.CheckConstraint('schema_version >= 1', name='dashboard_schema_positive'),
        sa.CheckConstraint('version >= 1', name='dashboard_version_positive'))


def downgrade():
    raise RuntimeError('Restore a verified complete backup with its matching program')
