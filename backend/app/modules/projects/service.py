"""Project facts never own or delete external source files or shared knowledge."""
from pathlib import Path
from uuid import uuid4, uuid5, NAMESPACE_URL
import os
from ...core.database import connection, HubError, utc_now
from ...core.operations import activity, record, replay
from ..knowledge.service import visible, versioned, create_resource, bump, rows
from ..library.notes.service import NoteService
from ..library.notes.schemas import NoteCreate
from ..life.tasks.schemas import TaskCreate
from ..life.tasks.models import tasks

PROGRESS_POLICY = '当前可见、未删除且未取消的项目任务：已完成 / 总数；里程碑独立手动验收，无任务时不计算百分比'


class ProjectService:
    def __init__(self, database):
        self.database = database

    def _get(self, c, pid, trash=False):
        r = visible(c, pid, trash=trash)
        if r['kind']!='project':
            raise HubError('PROJECT_NOT_FOUND', 404)
        r.update(rows(c, 'SELECT * FROM projects WHERE resource_id=?', (pid,))[0])
        r.pop('resource_id', None)
        from ..tags.service import tag_map
        r['tags'] = tag_map(c, [pid])[pid]
        return r

    def _editable(self, c, pid):
        r = self._get(c, pid)
        if r['status']=='archived':
            raise HubError('PROJECT_ARCHIVED', 409)
        return r

    def _milestone(self, c, pid, mid, trash=False):
        if mid is None:
            return None
        found = rows(c, 'SELECT * FROM project_milestones WHERE project_id=? AND id=?'+('' if trash else ' AND deleted_at IS NULL'), (pid, mid))
        if not found:
            raise HubError('MILESTONE_NOT_FOUND', 404)
        return found[0]

    def _progress(self, c, pid, mid=None):
        sql = """SELECT count(*) AS total, coalesce(sum(t.status='done'),0) AS done
            FROM project_tasks p JOIN resources r ON r.id=p.task_id JOIN tasks t ON t.resource_id=r.id
            WHERE p.project_id=? AND r.privacy_level='personal' AND r.deleted_at IS NULL AND t.status<>'cancelled'"""
        args = (pid,)
        if mid:
            sql += ' AND p.milestone_id=?'
            args += (mid,)
        result = rows(c, sql, args)[0]
        return {**result, 'percent':result['done']*100//result['total'] if result['total'] else None, 'denominator':PROGRESS_POLICY}

    def get(self, pid):
        with connection(self.database) as c:
            return {**self._get(c, pid), 'progress':self._progress(c, pid)}

    def list(self, q='', status='', trash=False, tag_id=None, limit=30, offset=0, recent=False):
        where = "r.kind='project' AND r.privacy_level='personal' AND r.deleted_at IS "+('NOT NULL' if trash else 'NULL')
        where += " AND instr(lower(r.title),lower(?))>0 AND (?='' OR p.status=?) AND (? IS NULL OR EXISTS(SELECT 1 FROM resource_tags rt WHERE rt.resource_id=r.id AND rt.tag_id=?))"
        args = (q, status, status, tag_id, tag_id)
        if recent:
            where += ' AND p.last_opened_at IS NOT NULL'
        with connection(self.database) as c:
            total = c.exec_driver_sql('SELECT count(*) FROM resources r JOIN projects p ON p.resource_id=r.id WHERE '+where, args).scalar_one()
            order = 'p.last_opened_at DESC,r.id' if recent else 'r.updated_at DESC,r.id'
            found = rows(c, 'SELECT r.id FROM resources r JOIN projects p ON p.resource_id=r.id WHERE '+where+' ORDER BY '+order+' LIMIT ? OFFSET ?', args+(limit,offset))
            return {'items':[{**self._get(c, r['id'], trash), 'progress':self._progress(c, r['id'])} for r in found], 'total':total, 'limit':limit, 'offset':offset}

    def save(self, payload, key, pid=None):
        data = payload.model_dump(mode='json')
        operation = 'project.save:'+str(pid)
        with connection(self.database, write=True) as c:
            if pid:
                self._get(c, pid)
            prior = replay(c, operation, key, data)
            if prior:
                return self._get(c, prior['resource_id'])
            fields = {k:v for k,v in data.items() if k not in ('title','expected_version')}
            if pid:
                versioned(c, pid, payload.expected_version)
                c.exec_driver_sql('UPDATE resources SET title=? WHERE id=?', (payload.title,pid))
                c.exec_driver_sql('UPDATE projects SET '+','.join(k+'=?' for k in fields)+' WHERE resource_id=?', (*fields.values(),pid))
                bump(c, pid)
            else:
                pid = create_resource(c, 'project', payload.title)
                c.exec_driver_sql('INSERT INTO projects(resource_id,'+','.join(fields)+') VALUES('+','.join('?' for _ in range(len(fields)+1))+')', (pid,*fields.values()))
            activity(c, pid, 'saved', fields.keys())
            record(c, operation, key, data, {'resource_id':pid})
            return self._get(c, pid)

    def opened(self, pid):
        # Explicit navigation action. GET remains side-effect free; no task/version changes.
        with connection(self.database, write=True) as c:
            self._get(c, pid)
            c.exec_driver_sql('UPDATE projects SET last_opened_at=? WHERE resource_id=?', (utc_now(),pid))
        return {'status':'recorded'}

    def directory_check(self, pid):
        with connection(self.database) as c:
            path = self._get(c, pid)['local_directory']
        if not path:
            return {'status':'not_configured','scope':'directory_metadata_only'}
        fd = None
        try:
            fd = os.open('/', os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
            for part in Path(path).parts[1:]:
                child = os.open(part, os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = child
            status = 'available'
        except FileNotFoundError:
            status = 'missing'
        except OSError:
            status = 'unavailable_or_unsafe'
        finally:
            if fd is not None:
                os.close(fd)
        return {'status':status, 'scope':'directory_metadata_only'}

    def trash(self, pid, payload, key, restore=False):
        data = payload.model_dump(mode='json')
        operation = ('project.restore:' if restore else 'project.trash:')+pid
        with connection(self.database, write=True) as c:
            r = self._get(c, pid, True)
            if replay(c, operation, key, data) is None:
                versioned(c, pid, payload.expected_version, trash=True)
                if bool(r['deleted_at'])!=restore:
                    raise HubError('PROJECT_STATE_CONFLICT')
                c.exec_driver_sql('UPDATE resources SET deleted_at=? WHERE id=?', (None if restore else utc_now(),pid))
                bump(c, pid)
                activity(c, pid, 'restored' if restore else 'trashed', ['deleted_at'])
                record(c, operation, key, data, {'resource_id':pid})
            return self._get(c, pid, True)

    def milestones(self, pid, trash=False):
        with connection(self.database) as c:
            self._get(c, pid)
            found = rows(c, 'SELECT * FROM project_milestones WHERE project_id=? AND deleted_at IS '+('NOT NULL' if trash else 'NULL')+' ORDER BY position,id', (pid,))
            return {'items':[{**m,'progress':self._progress(c,pid,m['id'])} for m in found]}

    def milestone_save(self, pid, payload, key, mid=None):
        data = payload.model_dump(mode='json')
        operation = 'project.milestone.save:'+pid+':'+str(mid)
        with connection(self.database, write=True) as c:
            self._get(c, pid)
            prior = replay(c, operation, key, data)
            if prior:
                return self._milestone(c, pid, prior['milestone_id'], True)
            self._editable(c, pid)
            versioned(c, pid, payload.expected_version)
            fields = {k:v for k,v in data.items() if k not in ('expected_version','expected_milestone_version')}
            now = utc_now()
            if mid:
                current = self._milestone(c, pid, mid)
                if current['version']!=payload.expected_milestone_version:
                    raise HubError('VERSION_CONFLICT')
                c.exec_driver_sql('UPDATE project_milestones SET '+','.join(k+'=?' for k in fields)+',version=version+1,updated_at=? WHERE id=?', (*fields.values(),now,mid))
            else:
                if c.exec_driver_sql('SELECT count(*) FROM project_milestones WHERE project_id=?', (pid,)).scalar_one()>=1000:
                    raise HubError('PROJECT_MILESTONE_LIMIT', 422)
                mid = str(uuid4())
                c.exec_driver_sql('INSERT INTO project_milestones(id,project_id,'+','.join(fields)+',created_at,updated_at) VALUES('+','.join('?' for _ in range(len(fields)+4))+')', (mid,pid,*fields.values(),now,now))
            bump(c, pid)
            activity(c, pid, 'milestone_saved', fields.keys())
            record(c, operation, key, data, {'milestone_id':mid})
            return self._milestone(c, pid, mid)

    def milestone_trash(self, pid, mid, payload, key, restore=False):
        data = payload.model_dump(mode='json')
        operation = ('project.milestone.restore:' if restore else 'project.milestone.trash:')+pid+':'+mid
        with connection(self.database, write=True) as c:
            self._get(c, pid)
            current = self._milestone(c, pid, mid, True)
            if replay(c, operation, key, data) is None:
                self._editable(c, pid)
                versioned(c, pid, payload.expected_version)
                if current['version']!=payload.expected_milestone_version:
                    raise HubError('VERSION_CONFLICT')
                if bool(current['deleted_at'])!=restore:
                    raise HubError('MILESTONE_STATE_CONFLICT')
                c.exec_driver_sql('UPDATE project_milestones SET deleted_at=?,updated_at=?,version=version+1 WHERE id=?', (None if restore else utc_now(),utc_now(),mid))
                bump(c, pid)
                activity(c, pid, 'milestone_restored' if restore else 'milestone_trashed', ['milestone'])
                record(c, operation, key, data, {'milestone_id':mid})
            return self._milestone(c, pid, mid, True)

    def task_list(self, pid, limit=30, offset=0):
        with connection(self.database) as c:
            self._get(c, pid)
            where = "p.project_id=? AND r.privacy_level='personal' AND r.deleted_at IS NULL"
            total = c.exec_driver_sql('SELECT count(*) FROM project_tasks p JOIN resources r ON r.id=p.task_id WHERE '+where, (pid,)).scalar_one()
            found = rows(c, """SELECT p.id AS link_id,r.id,r.title,r.version,t.status,t.priority,t.planned_date,
                CASE WHEN m.deleted_at IS NULL THEN m.id END AS milestone_id,
                CASE WHEN m.deleted_at IS NULL THEN m.title END AS milestone_title
                FROM project_tasks p JOIN resources r ON r.id=p.task_id JOIN tasks t ON t.resource_id=r.id
                LEFT JOIN project_milestones m ON m.id=p.milestone_id WHERE """+where+' ORDER BY r.created_at DESC,r.id LIMIT ? OFFSET ?', (pid,limit,offset))
            return {'items':found,'total':total,'limit':limit,'offset':offset}

    def _attach_task(self, c, pid, tid, mid):
        if visible(c, tid)['kind']!='task':
            raise HubError('TASK_NOT_FOUND', 404)
        self._milestone(c, pid, mid)
        existing = rows(c, 'SELECT project_id,milestone_id FROM project_tasks WHERE task_id=?', (tid,))
        if existing:
            if existing[0]['project_id']!=pid:
                raise HubError('TASK_ALREADY_ASSIGNED', 409)
            c.exec_driver_sql('UPDATE project_tasks SET milestone_id=? WHERE task_id=?', (mid,tid))
        else:
            c.exec_driver_sql('INSERT INTO project_tasks VALUES(?,?,?,?,?)', (str(uuid4()),pid,tid,mid,utc_now()))

    def task_attach(self, pid, payload, key):
        data = payload.model_dump(mode='json')
        operation = 'project.task.attach:'+pid
        with connection(self.database, write=True) as c:
            self._get(c, pid)
            visible(c, str(payload.task_id))
            if replay(c, operation, key, data) is None:
                self._editable(c, pid)
                versioned(c, pid, payload.expected_version)
                self._attach_task(c, pid, str(payload.task_id), str(payload.milestone_id) if payload.milestone_id else None)
                bump(c, pid)
                activity(c, pid, 'task_assigned', ['task','milestone'])
                record(c, operation, key, data, {'task_id':str(payload.task_id)})
            return self._get(c, pid)

    def task_create(self, pid, payload, key):
        data = payload.model_dump(mode='json')
        operation = 'project.task.create:'+pid
        with connection(self.database, write=True) as c:
            self._get(c, pid)
            prior = replay(c, operation, key, data)
            if prior:
                return visible(c, prior['task_id'])
            self._editable(c, pid)
            versioned(c, pid, payload.expected_version)
            task = TaskCreate(**{k:v for k,v in data.items() if k not in ('expected_version','milestone_id')})
            fields = task.model_dump(mode='json')
            fields.update(task.values_for_storage())
            fields.pop('title')
            tid = create_resource(c, 'task', task.title)
            c.execute(tasks.insert().values(resource_id=tid, **fields, status='todo', completed_at=None, estimated_seconds=None, position=0))
            self._attach_task(c, pid, tid, str(payload.milestone_id) if payload.milestone_id else None)
            bump(c, pid)
            activity(c, tid, 'created', ['project'])
            activity(c, pid, 'task_created', ['task','milestone'])
            record(c, operation, key, data, {'task_id':tid})
            return visible(c, tid)

    def entries(self, pid, limit=30, offset=0):
        with connection(self.database) as c:
            self._get(c, pid)
            query = """ FROM project_entries p JOIN resources r ON r.id=p.note_id JOIN notes n ON n.resource_id=r.id
                WHERE p.project_id=? AND r.privacy_level='personal' AND r.deleted_at IS NULL"""
            total = c.exec_driver_sql('SELECT count(*)'+query,(pid,)).scalar_one()
            found = rows(c, 'SELECT p.id,p.note_id,p.entry_kind,p.created_at,r.title,r.version,n.note_kind'+query+' ORDER BY p.created_at DESC,p.id LIMIT ? OFFSET ?', (pid,limit,offset))
            return {'items':found,'total':total,'limit':limit,'offset':offset}

    def _attach_note(self, c, pid, nid, kind):
        if visible(c, nid)['kind']!='note':
            raise HubError('NOTE_NOT_FOUND', 404)
        existing = rows(c, 'SELECT id FROM project_entries WHERE project_id=? AND note_id=?', (pid,nid))
        if existing:
            c.exec_driver_sql('UPDATE project_entries SET entry_kind=? WHERE id=?', (kind,existing[0]['id']))
            return existing[0]['id']
        eid = str(uuid4())
        c.exec_driver_sql('INSERT INTO project_entries VALUES(?,?,?,?,?)', (eid,pid,nid,kind,utc_now()))
        return eid

    def entry_attach(self, pid, payload, key):
        data = payload.model_dump(mode='json')
        operation = 'project.entry.attach:'+pid
        with connection(self.database, write=True) as c:
            self._get(c, pid)
            visible(c, str(payload.note_id))
            if replay(c, operation, key, data) is None:
                self._editable(c, pid)
                versioned(c, pid, payload.expected_version)
                eid = self._attach_note(c, pid, str(payload.note_id), payload.entry_kind)
                bump(c, pid)
                activity(c, pid, 'entry_assigned', ['note','entry_kind'])
                record(c, operation, key, data, {'entry_id':eid})
            return self._get(c, pid)

    def entry_create(self, pid, payload, key):
        data = payload.model_dump(mode='json')
        operation = 'project.entry.create:'+pid
        with connection(self.database) as c:
            self._get(c, pid)
            prior = replay(c, operation, key, data)
            if prior:
                visible(c, prior['note_id'])
                return prior
            self._editable(c, pid)
            versioned(c, pid, payload.expected_version)
        child = str(uuid5(NAMESPACE_URL, 'personal-hub-project-entry:'+pid+':'+key))
        note = NoteService(self.database).create(NoteCreate(title=payload.title, body=payload.body,
            note_kind='experiment' if payload.entry_kind=='experiment' else 'knowledge'), child)[0]
        with connection(self.database, write=True) as c:
            self._get(c, pid)
            prior = replay(c, operation, key, data)
            if prior:
                visible(c, prior['note_id'])
                return prior
            self._editable(c, pid)
            versioned(c, pid, payload.expected_version)
            eid = self._attach_note(c, pid, note['id'], payload.entry_kind)
            bump(c, pid)
            activity(c, pid, 'entry_created', ['note','entry_kind'])
            result = {'entry_id':eid,'note_id':note['id']}
            record(c, operation, key, data, result)
            return result

    def unlink(self, pid, eid, version):
        # Used by dedicated routes and generic ResourceContext projections alike.
        with connection(self.database, write=True) as c:
            self._editable(c, pid)
            versioned(c, pid, version)
            for table, target in [('project_tasks','task_id'),('project_entries','note_id')]:
                edge = rows(c, 'SELECT '+target+' AS target_id FROM '+table+' WHERE id=? AND project_id=?', (eid,pid))
                if edge:
                    visible(c, edge[0]['target_id'])
                    c.exec_driver_sql('DELETE FROM '+table+' WHERE id=? AND project_id=?', (eid,pid))
                    bump(c, pid)
                    activity(c, pid, 'unlinked', [target])
                    return {'status':'unlinked'}
            raise HubError('RELATION_NOT_FOUND', 404)
