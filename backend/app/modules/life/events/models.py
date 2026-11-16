from sqlalchemy import Column, ForeignKey, Integer, Table, Text
from ....core.database import metadata
from ....core.resources import resources
from ..tasks.models import tasks

events = Table('events',metadata,
    Column('resource_id',Text,ForeignKey(resources.c.id,ondelete='CASCADE'),primary_key=True),
    Column('description',Text),Column('is_all_day',Integer,nullable=False),
    Column('start_at',Text),Column('end_at',Text),Column('start_date',Text),Column('end_date',Text),
    Column('timezone',Text,nullable=False),Column('location',Text),Column('status',Text,nullable=False),
    Column('task_id',Text,ForeignKey(tasks.c.resource_id,ondelete='RESTRICT')))
