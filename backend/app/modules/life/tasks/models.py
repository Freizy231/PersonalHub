from sqlalchemy import Column, ForeignKey, Integer, Table, Text
from ....core.database import metadata
from ....core.resources import resources

tasks = Table('tasks', metadata,
    Column('resource_id', Text, ForeignKey(resources.c.id, ondelete='CASCADE'), primary_key=True),
    Column('description', Text), Column('status', Text, nullable=False),
    Column('priority', Integer, nullable=False), Column('planned_date', Text),
    Column('due_date', Text), Column('due_at', Text), Column('timezone', Text, nullable=False),
    Column('completed_at', Text), Column('estimated_seconds', Integer),
    Column('position', Integer, nullable=False))
