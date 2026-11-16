"""Shared ordinary tags; only additive DDL, no changes to existing rows."""
from alembic import op
import sqlalchemy as sa

revision = '0005_ordinary_tags'
down_revision = '0004_dashboard_preferences'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('tags',
        sa.Column('id', sa.Text, primary_key=True),
        sa.Column('name', sa.Text, nullable=False),
        sa.Column('normalized_name', sa.Text, nullable=False, unique=True),
        sa.Column('version', sa.Integer, nullable=False),
        sa.Column('created_at', sa.Text, nullable=False),
        sa.Column('updated_at', sa.Text, nullable=False),
        sa.Column('deleted_at', sa.Text),
        sa.CheckConstraint('version >= 1', name='tag_version_positive'),
        sa.CheckConstraint('length(name) BETWEEN 1 AND 64', name='tag_name_bounded'),
        sa.CheckConstraint('length(normalized_name) BETWEEN 1 AND 64', name='tag_key_bounded'))
    op.create_table('resource_tags',
        sa.Column('resource_id', sa.Text, sa.ForeignKey('resources.id', ondelete='RESTRICT'), primary_key=True),
        sa.Column('tag_id', sa.Text, sa.ForeignKey('tags.id', ondelete='RESTRICT'), primary_key=True),
        sa.Column('created_at', sa.Text, nullable=False))
    op.create_index('ix_resource_tags_tag_resource', 'resource_tags', ['tag_id','resource_id'])


def downgrade():
    raise RuntimeError('Restore a verified complete backup with its matching program')
