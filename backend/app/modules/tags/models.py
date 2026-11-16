from sqlalchemy import Column, ForeignKey, Integer, Table, Text
from ...core.database import metadata

tags = Table('tags', metadata,
    Column('id', Text, primary_key=True), Column('name', Text, nullable=False),
    Column('normalized_name', Text, nullable=False, unique=True),
    Column('version', Integer, nullable=False), Column('created_at', Text, nullable=False),
    Column('updated_at', Text, nullable=False), Column('deleted_at', Text))
resource_tags = Table('resource_tags', metadata,
    Column('resource_id', Text, ForeignKey('resources.id', ondelete='RESTRICT'), primary_key=True),
    Column('tag_id', Text, ForeignKey('tags.id', ondelete='RESTRICT'), primary_key=True),
    Column('created_at', Text, nullable=False))
