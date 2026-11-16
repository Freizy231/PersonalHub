from sqlalchemy import Column, ForeignKey, Integer, Table, Text
from ....core.database import metadata
from ....core.resources import resources
notes=Table('notes',metadata,
    Column('resource_id',Text,ForeignKey('resources.id',ondelete='CASCADE'),primary_key=True),
    Column('note_kind',Text,nullable=False),Column('local_date',Text),Column('body_path',Text,nullable=False,unique=True),
    Column('content_sha256',Text,nullable=False),Column('format',Text,nullable=False))
revisions=Table('note_revisions',metadata,
    Column('id',Text,primary_key=True),Column('note_id',Text,ForeignKey('notes.resource_id',ondelete='CASCADE'),nullable=False),
    Column('version',Integer,nullable=False),Column('body_path',Text,nullable=False,unique=True),
    Column('content_sha256',Text,nullable=False),Column('title',Text,nullable=False),
    Column('note_kind',Text,nullable=False),Column('local_date',Text),Column('created_at',Text,nullable=False))
file_operations=Table('file_operations',metadata,
    Column('id',Text,primary_key=True),Column('resource_id',Text,nullable=False),Column('operation_type',Text,nullable=False),
    Column('temp_path',Text,nullable=False),Column('final_path',Text,nullable=False,unique=True),Column('new_hash',Text,nullable=False),
    Column('state',Text,nullable=False),Column('created_at',Text,nullable=False),Column('updated_at',Text,nullable=False))
