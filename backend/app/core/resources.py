"""Shared resource identity and activity; concrete business fields live elsewhere."""
from sqlalchemy import Column, ForeignKey, Integer, Table, Text
from .database import metadata

resources = Table('resources', metadata,
    Column('id', Text, primary_key=True), Column('kind', Text, nullable=False),
    Column('title', Text, nullable=False), Column('summary', Text),
    Column('privacy_level', Text, nullable=False), Column('created_at', Text, nullable=False),
    Column('updated_at', Text, nullable=False), Column('deleted_at', Text),
    Column('version', Integer, nullable=False))
activities = Table('activities', metadata,
    Column('id', Text, primary_key=True),
    Column('resource_id', Text, ForeignKey('resources.id', ondelete='SET NULL')),
    Column('action', Text, nullable=False), Column('actor', Text, nullable=False),
    Column('occurred_at', Text, nullable=False), Column('privacy_level', Text, nullable=False),
    Column('payload_json', Text, nullable=False))
operation_requests = Table('operation_requests', metadata,
    Column('id', Text, primary_key=True), Column('idempotency_key', Text, nullable=False, unique=True),
    Column('request_hash', Text, nullable=False), Column('operation_kind', Text, nullable=False),
    Column('status', Text, nullable=False), Column('result_json', Text), Column('expires_at', Text),
    Column('created_at', Text, nullable=False), Column('updated_at', Text, nullable=False))
