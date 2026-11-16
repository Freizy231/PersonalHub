"""Empty Personal Hub schema baseline. Never touches the legacy database."""
revision = "0000_foundation"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    raise RuntimeError("Restore a verified backup with its matching application version")
