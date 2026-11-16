from sqlalchemy import Column,ForeignKey,Integer,Table,Text
from ....core.database import metadata
from ....core.resources import resources

reminders=Table('reminders',metadata,
    Column('id',Text,primary_key=True),Column('resource_id',Text,ForeignKey(resources.c.id,ondelete='RESTRICT'),nullable=False),
    Column('remind_at',Text,nullable=False),Column('status',Text,nullable=False),Column('delivered_at',Text),
    Column('acknowledged_at',Text),Column('delivery_key',Text,nullable=False,unique=True),
    Column('created_at',Text,nullable=False),Column('updated_at',Text,nullable=False),Column('version',Integer,nullable=False))
