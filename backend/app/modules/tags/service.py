"""One ordinary Tag service; no access to vault contents or identities."""
from uuid import uuid4
from sqlalchemy import text, or_, and_, delete, func, select, update
from ...core.database import HubError, connection, utc_now
from ...core.resources import resources
from ...core.operations import activity, record, replay
from ..library.notes.models import notes
from ..life.tasks.models import tasks
from .models import tags, resource_tags
from .schemas import tag_name

from ..knowledge.service import EXTENSIONS,lineage_sql
SUPPORTED_EXTENSION = or_(*[and_(resources.c.kind == kind, text('EXISTS(SELECT 1 FROM '+table+' WHERE '+table+'.resource_id=resources.id)')) for kind,table in EXTENSIONS.items()])
VISIBLE = and_(text(lineage_sql()), SUPPORTED_EXTENSION, resources.c.privacy_level == 'personal', resources.c.deleted_at.is_(None))
JOIN = tags.join(resource_tags, tags.c.id == resource_tags.c.tag_id).join(resources, resources.c.id == resource_tags.c.resource_id)


def tag_map(con, ids):
    """Caller must already authorize these ordinary resource IDs, including export trash."""
    result = {rid: [] for rid in ids}
    if not ids: return result
    rows = con.execute(select(resource_tags.c.resource_id, tags.c.id, tags.c.name, tags.c.version)
        .select_from(resource_tags.join(tags)).where(resource_tags.c.resource_id.in_(ids), tags.c.deleted_at.is_(None))
        .order_by(tags.c.normalized_name, tags.c.id)).mappings()
    for row in rows:
        result[row['resource_id']].append({k: row[k] for k in ('id', 'name', 'version')})
    return result


class TagService:
    def __init__(self, database): self.database = database

    def _resource(self, con, rid):
        row = con.execute(select(resources)
            .where(VISIBLE, resources.c.id == rid)).mappings().first()
        if row is None: raise HubError('RESOURCE_TAGS_NOT_FOUND', 404)
        return row

    def _tag(self, con, tid):
        row = con.execute(select(*tags.c, func.count().label('usage_count')).select_from(JOIN)
            .where(VISIBLE, tags.c.deleted_at.is_(None), tags.c.id == tid).group_by(tags.c.id)).mappings().first()
        if row is None: raise HubError('TAG_NOT_FOUND', 404)
        return {**{k: row[k] for k in ('id','name','version','created_at','updated_at','usage_count')}, 'counts_by_kind':self._counts(con,tid)}

    def _counts(self,con,tid):
        return dict(con.execute(select(resources.c.kind,func.count()).select_from(JOIN).where(VISIBLE,tags.c.id==tid).group_by(resources.c.kind)).all())

    def _get(self, con, rid):
        resource = self._resource(con, rid)
        # At most 20 entries. Count only currently visible ordinary notes.
        entries = tag_map(con, [rid])[rid]
        return {'resource_id': rid, 'resource_version': resource['version'],
                'tags': [self._tag(con, t['id']) for t in entries]}

    def get(self, rid):
        with connection(self.database) as con: return self._get(con, rid)

    def list(self, q='', limit=50, offset=0):
        clauses = [VISIBLE, tags.c.deleted_at.is_(None)]
        if q.strip():
            try: normalized = tag_name(q).casefold()
            except ValueError: raise HubError('TAG_QUERY_INVALID', 422) from None
            escaped = normalized.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
            clauses.append(tags.c.normalized_name.like('%' + escaped + '%', escape='\\'))
        grouped = select(tags.c.id).select_from(JOIN).where(*clauses).group_by(tags.c.id)
        with connection(self.database) as con:
            total = con.execute(select(func.count()).select_from(grouped.subquery())).scalar_one()
            rows = con.execute(select(tags.c.id, tags.c.name, tags.c.version, func.count().label('usage_count'))
                .select_from(JOIN).where(*clauses).group_by(tags.c.id)
                .order_by(tags.c.normalized_name, tags.c.id).limit(limit).offset(offset)).mappings()
            return {'items': [{**dict(r),'counts_by_kind':self._counts(con,r['id'])} for r in rows], 'total':total, 'limit':limit, 'offset':offset}

    def assign(self, rid, payload, key):
        values = payload.model_dump(mode='json'); operation = 'resource.tags:' + rid
        with connection(self.database, write=True) as con:
            current = self._resource(con, rid)
            if replay(con, operation, key, values) is not None: return self._get(con, rid)
            if current['version'] != payload.expected_version: raise HubError('VERSION_CONFLICT')
            assigned = tag_map(con, [rid])[rid]
            if {t['id']:t['version'] for t in assigned} != values['expected_tag_versions']:
                raise HubError('VERSION_CONFLICT')
            old_ids = set(con.execute(select(resource_tags.c.tag_id).where(resource_tags.c.resource_id == rid)).scalars())
            desired = set(); instant = utc_now()
            for name in payload.names:
                normalized = name.casefold()
                row = con.execute(select(tags).where(tags.c.normalized_name == normalized)).mappings().first()
                if row and row['deleted_at'] is not None: raise HubError('TAG_NAME_RESERVED')
                if row: tid = row['id']
                else:
                    tid = str(uuid4())
                    con.execute(tags.insert().values(id=tid, name=name, normalized_name=normalized, version=1,
                        created_at=instant, updated_at=instant, deleted_at=None))
                desired.add(tid)
            if desired != old_ids:
                removed = old_ids - desired
                if removed: con.execute(delete(resource_tags).where(resource_tags.c.resource_id == rid, resource_tags.c.tag_id.in_(removed)))
                for tid in desired - old_ids:
                    con.execute(resource_tags.insert().values(resource_id=rid, tag_id=tid, created_at=instant))
                con.execute(update(resources).where(resources.c.id == rid).values(version=current['version']+1, updated_at=instant))
                activity(con, rid, 'tags_updated', ['tags'])
            record(con, operation, key, values, {'resource_id':rid})
            return self._get(con, rid)

    def rename(self, tid, payload, key):
        values = payload.model_dump(mode='json'); operation = 'tag.rename:' + tid
        with connection(self.database, write=True) as con:
            current = self._tag(con, tid)
            if replay(con, operation, key, values) is not None: return current
            if current['version'] != payload.expected_version: raise HubError('VERSION_CONFLICT')
            duplicate = con.execute(select(tags.c.id).where(tags.c.normalized_name == payload.name.casefold(), tags.c.id != tid)).first()
            if duplicate: raise HubError('TAG_NAME_CONFLICT')
            if current['name'] != payload.name:
                con.execute(update(tags).where(tags.c.id == tid).values(name=payload.name, normalized_name=payload.name.casefold(),
                    version=current['version']+1, updated_at=utc_now()))
            record(con, operation, key, values, {'tag_id':tid})
            return self._tag(con, tid)
