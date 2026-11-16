"""Ordinary task facts. Identity, task and activity commit in one transaction."""
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import uuid
from zoneinfo import ZoneInfo

from sqlalchemy import and_, case, func, or_, select, update

from ....core.database import HubError, connection, utc_now, SCHEMA_REVISION
from ....core.resources import activities, operation_requests, resources
from .models import tasks
from ...tags.service import tag_map
from ...tags.models import resource_tags
from ..reminders.service import cancel_pending
from .schemas import TaskCreate, TaskPatch

JOIN = resources.join(tasks, resources.c.id == tasks.c.resource_id)
VISIBLE = and_(resources.c.kind == 'task', resources.c.privacy_level == 'personal')
COLUMNS = [*resources.c, *[c for c in tasks.c if c.name != 'resource_id']]


def task_dict(row):
    result = dict(row)
    instant = datetime.now(timezone.utc)
    result['is_overdue'] = result['status'] in ('todo', 'doing') and (
        (result['due_date'] is not None and result['due_date'] < instant.astimezone(ZoneInfo(result['timezone'])).date().isoformat())
        or (result['due_at'] is not None and result['due_at'] < instant.isoformat(timespec='microseconds').replace('+00:00', 'Z')))
    return result


class TaskService:
    def __init__(self, database: Path):
        self.database = database

    def _get(self, con, resource_id, *, deleted=False):
        query = select(*COLUMNS).select_from(JOIN).where(VISIBLE, resources.c.id == resource_id)
        if not deleted:
            query = query.where(resources.c.deleted_at.is_(None))
        row = con.execute(query).mappings().first()
        if row is None:
            raise HubError('TASK_NOT_FOUND', 404)
        result=task_dict(row)
        result['tags']=tag_map(con,[resource_id])[resource_id]
        return result

    def get(self, resource_id):
        with connection(self.database) as con:
            return self._get(con, resource_id)

    def list(self, scope='all', on_date=None, limit=50, offset=0, tag_id=None):
        on_date = on_date or date.today().isoformat()
        clauses = [VISIBLE, resources.c.deleted_at.is_not(None) if scope == 'trash' else resources.c.deleted_at.is_(None)]
        if tag_id:clauses.append(resources.c.id.in_(select(resource_tags.c.resource_id).where(resource_tags.c.tag_id == tag_id)))
        if scope == 'today':
            clauses.append(tasks.c.planned_date == on_date)
        elif scope == 'upcoming':
            clauses.extend([tasks.c.status.in_(['todo','doing']), tasks.c.planned_date > on_date])
        elif scope == 'overdue':
            clauses.extend([tasks.c.status.in_(['todo','doing']), or_(
                tasks.c.due_date < func.hub_today(tasks.c.timezone), tasks.c.due_at < utc_now())])
        elif scope == 'completed':
            clauses.append(tasks.c.status == 'done')
        with connection(self.database) as con:
            total = con.execute(select(func.count()).select_from(JOIN).where(*clauses)).scalar_one()
            rows = con.execute(select(*COLUMNS).select_from(JOIN).where(*clauses).order_by(
                case((tasks.c.status.in_(['done','cancelled']), 1), else_=0),
                tasks.c.priority.desc(), tasks.c.position,
                func.coalesce(tasks.c.due_date, func.substr(tasks.c.due_at, 1, 10), '9999-12-31'),
                resources.c.created_at.desc(), resources.c.id).limit(limit).offset(offset)).mappings()
            items=[task_dict(r) for r in rows]; mapped=tag_map(con,[r['id'] for r in items])
            for r in items:r['tags']=mapped[r['id']]
            return {'items': items, 'total': total, 'limit': limit, 'offset': offset}

    def _activity(self, con, resource_id, action, fields):
        con.execute(activities.insert().values(id=str(uuid.uuid4()), resource_id=resource_id, action=action,
            actor='user', occurred_at=utc_now(), privacy_level='personal',
            payload_json=json.dumps({'fields': sorted(fields)}, separators=(',', ':'))))

    def create(self, payload: TaskCreate, idempotency_key: str):
        data = payload.model_dump(mode='json')
        data.update(payload.values_for_storage())
        fingerprint = hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        with connection(self.database, write=True) as con:
            prior = con.execute(select(operation_requests).where(operation_requests.c.idempotency_key == idempotency_key)).mappings().first()
            if prior:
                if prior['request_hash'] != fingerprint or prior['operation_kind'] != 'task.create':
                    raise HubError('IDEMPOTENCY_CONFLICT')
                result = self._get(con, json.loads(prior['result_json'])['resource_id'], deleted=True)
                if result['deleted_at'] is not None:
                    raise HubError('IDEMPOTENCY_RESULT_TRASHED')
                return result, True
            resource_id, instant = str(uuid.uuid4()), utc_now()
            title = data.pop('title')
            con.execute(resources.insert().values(id=resource_id, kind='task', title=title, summary=None,
                privacy_level='personal', created_at=instant, updated_at=instant, deleted_at=None, version=1))
            con.execute(tasks.insert().values(resource_id=resource_id, **data, status='todo', completed_at=None,
                                              estimated_seconds=None, position=0))
            self._activity(con, resource_id, 'created', ['title', *data.keys()])
            con.execute(operation_requests.insert().values(id=str(uuid.uuid4()), idempotency_key=idempotency_key,
                request_hash=fingerprint, operation_kind='task.create', status='applied',
                result_json=json.dumps({'resource_id': resource_id}), expires_at=None, created_at=instant, updated_at=instant))
            return self._get(con, resource_id), False

    def change(self, resource_id, payload: TaskPatch):
        data = payload.values_for_storage()
        expected = data.pop('expected_version')
        with connection(self.database, write=True) as con:
            current = self._get(con, resource_id)
            if current['version'] != expected:
                raise HubError('VERSION_CONFLICT')
            merged = {**current, **data}
            if merged['due_date'] and merged['due_at']:
                raise HubError('DEADLINE_CONFLICT', 422)
            meaningful = {key: value for key, value in data.items() if current[key] != value}
            if not meaningful:
                return current
            changed_fields = set(meaningful)
            resource_changes = {'updated_at': utc_now(), 'version': expected + 1}
            if 'title' in meaningful:
                resource_changes['title'] = meaningful.pop('title')
            if 'status' in meaningful:
                meaningful['completed_at'] = utc_now() if meaningful['status'] == 'done' else None
            con.execute(update(resources).where(resources.c.id == resource_id, resources.c.version == expected).values(**resource_changes))
            if meaningful:
                con.execute(update(tasks).where(tasks.c.resource_id == resource_id).values(**meaningful))
            if {'due_date','due_at','timezone'} & changed_fields or merged['status'] in ('done','cancelled'):
                cancel_pending(con,resource_id)
            # "completed" only when this edit actually moved the task to done, not when a
            # done task is re-saved with an unchanged status alongside another change.
            action = 'completed' if 'status' in changed_fields and merged['status'] == 'done' else 'updated'
            self._activity(con, resource_id, action, data.keys())
            return self._get(con, resource_id)

    def trash(self, resource_id, expected_version, *, restore=False):
        with connection(self.database, write=True) as con:
            current = self._get(con, resource_id, deleted=True)
            if current['version'] != expected_version:
                raise HubError('VERSION_CONFLICT')
            if bool(current['deleted_at']) != restore:
                raise HubError('TASK_STATE_CONFLICT')
            con.execute(update(resources).where(resources.c.id == resource_id).values(
                deleted_at=None if restore else utc_now(), updated_at=utc_now(), version=expected_version + 1))
            if not restore: cancel_pending(con,resource_id)
            self._activity(con, resource_id, 'restored' if restore else 'trashed', ['deleted_at'])
            return self._get(con, resource_id, deleted=True)

    def export(self):
        with connection(self.database) as con:
            rows = list(con.execute(select(*COLUMNS).select_from(JOIN).where(VISIBLE).order_by(resources.c.id)).mappings())
            history = con.execute(select(activities).join(resources, activities.c.resource_id == resources.c.id).where(
                VISIBLE, activities.c.privacy_level == 'personal').order_by(activities.c.occurred_at, activities.c.id)).mappings()
            mapped=tag_map(con,[r['id'] for r in rows])
            return {'format': 'personal-hub-task-export', 'format_version': 1, 'created_at': utc_now(),
                    'schema_revision': SCHEMA_REVISION, 'includes_trash': True, 'excludes': ['private', 'secrets', 'legacy_intelligence'],
                    'tasks': [{**dict(r),'tags':mapped[r['id']]} for r in rows], 'activities': [dict(r) for r in history]}
