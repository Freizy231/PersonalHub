"""Calendar facts; associations never implicitly modify tasks."""
from datetime import date, datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import uuid
from sqlalchemy import and_, or_, select, update, func
from ....core.database import HubError, connection, utc_now, SCHEMA_REVISION
from ....core.resources import resources, activities
from ....core.operations import replay, record, activity
from ..tasks.models import tasks
from .models import events
from .schemas import canonical

JOIN = resources.join(events, resources.c.id == events.c.resource_id)
VISIBLE = and_(resources.c.kind == 'event', resources.c.privacy_level == 'personal')
COLUMNS = [*resources.c, *[c for c in events.c if c.name != 'resource_id']]
TIME_FIELDS = {'is_all_day','start_at','end_at','start_date','end_date','timezone'}


class EventService:
    def __init__(self, database): self.database = database

    def _dto(self, con, row):
        result = dict(row)
        result['is_all_day'] = bool(result['is_all_day'])
        result['linked_task'] = None
        if result['task_id']:
            target = con.execute(select(resources.c.id, resources.c.title, tasks.c.status).select_from(
                resources.join(tasks, resources.c.id == tasks.c.resource_id)).where(
                resources.c.id == result['task_id'], resources.c.kind == 'task',
                resources.c.privacy_level == 'personal', resources.c.deleted_at.is_(None))).mappings().first()
            result['linked_task'] = dict(target) if target else None
            if not target: result['task_id'] = None
        return result

    def _get(self, con, resource_id, deleted=False):
        query = select(*COLUMNS).select_from(JOIN).where(VISIBLE, resources.c.id == resource_id)
        if not deleted: query = query.where(resources.c.deleted_at.is_(None))
        row = con.execute(query).mappings().first()
        if row is None: raise HubError('EVENT_NOT_FOUND',404)
        return self._dto(con,row)

    def get(self, resource_id):
        with connection(self.database) as con: return self._get(con,resource_id)

    def _validate_task(self, con, task_id):
        if task_id and not con.execute(select(tasks.c.resource_id).join(resources, tasks.c.resource_id == resources.c.id).where(
            tasks.c.resource_id == task_id, resources.c.kind == 'task', resources.c.privacy_level == 'personal',
            resources.c.deleted_at.is_(None))).first(): raise HubError('TASK_NOT_FOUND',404)

    def list(self, start_date, end_date, timezone='Asia/Shanghai', scope='calendar', limit=100, offset=0):
        try:
            start,end = date.fromisoformat(start_date),date.fromisoformat(end_date)
            zone = ZoneInfo(timezone)
        except (ValueError,ZoneInfoNotFoundError,OSError): raise HubError('CALENDAR_RANGE_INVALID',422) from None
        if not 0 < (end-start).days <= 62: raise HubError('CALENDAR_RANGE_INVALID',422)
        clauses = [VISIBLE, resources.c.deleted_at.is_not(None) if scope=='trash' else resources.c.deleted_at.is_(None)]
        if scope != 'trash':
            clauses.append(or_(and_(events.c.is_all_day == 1, events.c.start_date < end_date, events.c.end_date > start_date),
                and_(events.c.is_all_day == 0, events.c.start_at < canonical(datetime.combine(end,time.min,zone)),
                     events.c.end_at > canonical(datetime.combine(start,time.min,zone)))))
        with connection(self.database) as con:
            total = con.execute(select(func.count()).select_from(JOIN).where(*clauses)).scalar_one()
            rows = con.execute(select(*COLUMNS).select_from(JOIN).where(*clauses).order_by(
                func.coalesce(events.c.start_date,events.c.start_at),resources.c.id).limit(limit).offset(offset)).mappings()
            return {'items':[self._dto(con,r) for r in rows],'total':total,'limit':limit,'offset':offset}

    def create(self,payload,key):
        data = payload.storage()
        with connection(self.database,write=True) as con:
            prior = replay(con,'event.create',key,data)
            if prior:
                row = self._get(con,prior['resource_id'],deleted=True)
                if row['deleted_at']: raise HubError('IDEMPOTENCY_RESULT_TRASHED')
                return row,True
            self._validate_task(con,data['task_id'])
            rid,instant = str(uuid.uuid4()),utc_now()
            facts = dict(data);title = facts.pop('title')
            con.execute(resources.insert().values(id=rid,kind='event',title=title,summary=None,privacy_level='personal',
                created_at=instant,updated_at=instant,deleted_at=None,version=1))
            con.execute(events.insert().values(resource_id=rid,**facts))
            activity(con,rid,'created',data.keys())
            record(con,'event.create',key,data,{'resource_id':rid})
            return self._get(con,rid),False

    def change(self,rid,payload):
        from ..reminders.service import cancel_pending
        data = payload.storage();expected = data.pop('expected_version')
        with connection(self.database,write=True) as con:
            current = self._get(con,rid)
            if current['version'] != expected: raise HubError('VERSION_CONFLICT')
            # Omitted task_id preserves an association that may no longer be visible.
            if 'task_id' not in payload.model_fields_set: data.pop('task_id')
            else: self._validate_task(con,data['task_id'])
            changed = {k:v for k,v in data.items() if current[k] != v}
            if not changed: return current
            facts = dict(changed)
            resource_changes = {'updated_at':utc_now(),'version':expected+1}
            if 'title' in facts: resource_changes['title'] = facts.pop('title')
            con.execute(update(resources).where(resources.c.id==rid).values(**resource_changes))
            if facts: con.execute(update(events).where(events.c.resource_id==rid).values(**facts))
            if TIME_FIELDS & changed.keys() or data['status'] != 'scheduled': cancel_pending(con,rid)
            activity(con,rid,'updated',changed.keys())
            return self._get(con,rid)

    def trash(self,rid,expected_version,restore=False):
        from ..reminders.service import cancel_pending
        with connection(self.database,write=True) as con:
            current = self._get(con,rid,deleted=True)
            if current['version'] != expected_version: raise HubError('VERSION_CONFLICT')
            if bool(current['deleted_at']) != restore: raise HubError('EVENT_STATE_CONFLICT')
            con.execute(update(resources).where(resources.c.id==rid).values(deleted_at=None if restore else utc_now(),
                updated_at=utc_now(),version=expected_version+1))
            if not restore: cancel_pending(con,rid)
            activity(con,rid,'restored' if restore else 'trashed',['deleted_at'])
            return self._get(con,rid,deleted=True)

    def export(self):
        with connection(self.database) as con:
            rows = con.execute(select(*COLUMNS).select_from(JOIN).where(VISIBLE).order_by(resources.c.id)).mappings()
            history = con.execute(select(activities).join(resources,activities.c.resource_id==resources.c.id).where(
                VISIBLE,activities.c.privacy_level=='personal').order_by(activities.c.occurred_at,activities.c.id)).mappings()
            return {'format':'personal-hub-event-export','format_version':1,'schema_revision':SCHEMA_REVISION,
                'created_at':utc_now(),'includes_trash':True,'excludes':['private','secrets','legacy_intelligence'],
                'events':[self._dto(con,r) for r in rows],'activities':[dict(r) for r in history]}
