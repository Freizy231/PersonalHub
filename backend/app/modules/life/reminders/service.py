"""Durable one-shot inbox. Delivered means committed, not read by a browser."""
from datetime import datetime, timedelta, timezone
import uuid
from sqlalchemy import and_, or_, select, update, func
from ....core.database import HubError, connection, utc_now, SCHEMA_REVISION
from ....core.resources import resources
from ....core.operations import replay, record, activity
from ..tasks.models import tasks
from ..events.models import events
from ..events.schemas import canonical
from .models import reminders

JOIN = reminders.join(resources,reminders.c.resource_id==resources.c.id)
VISIBLE = and_(resources.c.privacy_level=='personal', resources.c.deleted_at.is_(None),resources.c.kind.in_(['task','event']))
COLUMNS = [*reminders.c,resources.c.title,resources.c.kind]


def cancel_pending(con,rid):
    con.execute(update(reminders).where(reminders.c.resource_id==rid,reminders.c.status=='pending').values(
        status='cancelled',updated_at=utc_now(),version=reminders.c.version+1))


def eligible(con,rid):
    row = con.execute(select(resources).where(resources.c.id==rid,resources.c.privacy_level=='personal',
        resources.c.deleted_at.is_(None))).mappings().first()
    if not row:return None
    table = tasks if row['kind']=='task' else events if row['kind']=='event' else None
    if table is None:return None
    status = con.execute(select(table.c.status).where(table.c.resource_id==rid)).scalar_one_or_none()
    return row if status in (['todo','doing'] if row['kind']=='task' else ['scheduled']) else None


class ReminderService:
    def __init__(self,database):self.database=database

    def _get(self,con,rid):
        row=con.execute(select(*COLUMNS).select_from(JOIN).where(VISIBLE,reminders.c.id==rid)).mappings().first()
        if row is None:raise HubError('REMINDER_NOT_FOUND',404)
        result=dict(row);result.pop('delivery_key')
        return result

    def create(self,payload,key):
        data=payload.model_dump(mode='json');data['remind_at']=canonical(payload.remind_at)
        with connection(self.database,write=True) as con:
            prior=replay(con,'reminder.create',key,data)
            if prior:return self._get(con,prior['reminder_id']),True
            target=eligible(con,data['resource_id'])
            if target is None:raise HubError('REMINDER_TARGET_UNAVAILABLE',409)
            if target['version']!=data['expected_resource_version']:raise HubError('VERSION_CONFLICT')
            rid,instant=str(uuid.uuid4()),utc_now()
            con.execute(reminders.insert().values(id=rid,resource_id=data['resource_id'],remind_at=data['remind_at'],
                status='pending',delivered_at=None,acknowledged_at=None,delivery_key=key,created_at=instant,updated_at=instant,version=1))
            activity(con,data['resource_id'],'reminder_scheduled',['remind_at'])
            record(con,'reminder.create',key,data,{'reminder_id':rid})
            return self._get(con,rid),False

    def list(self,scope='inbox',resource_id=None,limit=50,offset=0):
        clauses=[VISIBLE]
        if resource_id:clauses.append(reminders.c.resource_id==resource_id)
        if scope=='inbox':clauses.extend([reminders.c.status.in_(['delivered','missed']),reminders.c.acknowledged_at.is_(None)])
        elif scope=='pending':clauses.append(reminders.c.status=='pending')
        with connection(self.database) as con:
            total=con.execute(select(func.count()).select_from(JOIN).where(*clauses)).scalar_one()
            rows=con.execute(select(reminders.c.id).select_from(JOIN).where(*clauses).order_by(
                reminders.c.remind_at.desc(),reminders.c.id).limit(limit).offset(offset)).scalars().all()
            return {'items':[self._get(con,r) for r in rows],'total':total,'limit':limit,'offset':offset}

    def change(self,rid,expected,action):
        with connection(self.database,write=True) as con:
            current=self._get(con,rid)
            # A lost ack response is safe to retry, including the previous version.
            if action=='ack' and current['acknowledged_at']:return current
            if action=='cancel' and current['status']=='cancelled':return current
            if current['version']!=expected:raise HubError('VERSION_CONFLICT')
            if action=='ack' and current['status'] not in ['delivered','missed']:raise HubError('REMINDER_STATE_CONFLICT')
            if action=='cancel' and current['status']!='pending':raise HubError('REMINDER_STATE_CONFLICT')
            values={'updated_at':utc_now(),'version':expected+1}
            values.update({'acknowledged_at':utc_now()} if action=='ack' else {'status':'cancelled'})
            con.execute(update(reminders).where(reminders.c.id==rid).values(**values))
            activity(con,current['resource_id'],'reminder_'+action,['status' if action=='cancel' else 'acknowledged_at'])
            return self._get(con,rid)

    def drain(self,now=None):
        instant=now or datetime.now(timezone.utc)
        if instant.tzinfo is None:raise ValueError('aware clock required')
        timestamp,cutoff=canonical(instant),canonical(instant-timedelta(hours=24))
        counts={'delivered':0,'missed':0,'cancelled':0}
        with connection(self.database,write=True) as con:
            # Bounded transaction: catch up in later ticks if more are due.
            rows=con.execute(select(reminders).where(reminders.c.status=='pending').order_by(
                reminders.c.remind_at,reminders.c.id).limit(500)).mappings().all()
            for row in rows:
                if eligible(con,row['resource_id']) is None:status='cancelled'
                elif row['remind_at']>timestamp:continue
                else:status='missed' if row['remind_at']<cutoff else 'delivered'
                con.execute(update(reminders).where(reminders.c.id==row['id'],reminders.c.status=='pending').values(
                    status=status,delivered_at=timestamp if status=='delivered' else None,updated_at=timestamp,version=row['version']+1))
                counts[status]+=1
        return counts

    def export(self):
        with connection(self.database) as con:
            ids=con.execute(select(reminders.c.id).select_from(JOIN).where(
                resources.c.privacy_level=='personal',resources.c.kind.in_(['task','event']))).scalars().all()
            # Include soft-deleted resource reminders in portable export, without private titles.
            rows=con.execute(select(reminders).select_from(JOIN).where(
                resources.c.privacy_level=='personal',reminders.c.id.in_(ids)).order_by(reminders.c.id)).mappings()
            return {'format':'personal-hub-reminder-export','format_version':1,'schema_revision':SCHEMA_REVISION,
                'created_at':utc_now(),'includes_trash':True,'reminders':[{k:v for k,v in r.items() if k!='delivery_key'} for r in rows]}
