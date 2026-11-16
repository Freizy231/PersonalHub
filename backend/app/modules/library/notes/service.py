"""Ordinary notes: immutable files first, resource pointers last; no implicit repair."""
from io import BytesIO
import json
from pathlib import Path
import uuid
import zipfile
from sqlalchemy import and_,select,update,func
from ....core.database import HubError,connection,utc_now,SCHEMA_REVISION
from ....core.resources import resources,activities
from ....core.operations import replay,record,activity
from ....core.note_files import NoteFiles,file_lock,digest
from .models import notes,revisions,file_operations
from .schemas import NoteCreate,NoteEdit
from ...tags.models import tags,resource_tags
from ...tags.service import tag_map

JOIN=resources.join(notes,resources.c.id==notes.c.resource_id)
VISIBLE=and_(resources.c.kind=='note',resources.c.privacy_level=='personal')
COLUMNS=[*resources.c,*[c for c in notes.c if c.name!='resource_id']]


class NoteService:
    def __init__(self,database,root=None):
        self.database=Path(database)
        self.root=Path(root) if root is not None else self.database.parent.parent
        self.files=NoteFiles(self.root)

    def _get(self,con,rid,deleted=False,body=True):
        query=select(*COLUMNS).select_from(JOIN).where(VISIBLE,resources.c.id==rid)
        if not deleted:query=query.where(resources.c.deleted_at.is_(None))
        row=con.execute(query).mappings().first()
        if row is None:raise HubError('NOTE_NOT_FOUND',404)
        result=dict(row)
        if body:result['body']=self.files.body(result['body_path'],result['content_sha256'])
        return result

    def get(self,rid):
        with connection(self.database) as con:return self._get(con,rid)

    def list(self,scope='all',limit=20,offset=0,tag_id=None):
        clauses=[VISIBLE,resources.c.deleted_at.is_not(None) if scope=='trash' else resources.c.deleted_at.is_(None)]
        if scope in ['quick','journal','knowledge','experiment']:clauses.append(notes.c.note_kind==scope)
        if tag_id is not None:
            clauses.append(resources.c.id.in_(select(resource_tags.c.resource_id).join(tags).where(
                resource_tags.c.tag_id==tag_id,tags.c.deleted_at.is_(None))))
        with connection(self.database) as con:
            total=con.execute(select(func.count()).select_from(JOIN).where(*clauses)).scalar_one()
            rows=con.execute(select(*COLUMNS).select_from(JOIN).where(*clauses).order_by(
                resources.c.updated_at.desc(),resources.c.id).limit(limit).offset(offset)).mappings().all()
            assigned=tag_map(con,[row['id'] for row in rows])
            results=[]
            for row in rows:
                result=dict(row);result['tags']=assigned[row['id']];result['storage_state']='ready'
                try:self.files.body(result['body_path'],result['content_sha256'])
                except HubError as error:
                    if error.code not in ['NOTE_FILE_MISSING','NOTE_FILE_CONFLICT','NOTE_FILE_UNSAFE','NOTE_PATH_UNSAFE']:raise
                    result['storage_state']='missing' if error.code=='NOTE_FILE_MISSING' else 'conflict'
                results.append(result)
            return {'items':results,'total':total,'limit':limit,'offset':offset}

    def _prior(self,con,kind,key,data):
        prior=replay(con,kind,key,data)
        if prior:
            result=self._get(con,prior['resource_id'],deleted=True)
            if result['deleted_at']:raise HubError('IDEMPOTENCY_RESULT_TRASHED')
            return result
        return None

    def _prepare(self,rid,body):
        operation_id,revision_id=str(uuid.uuid4()),str(uuid.uuid4())
        path=f'notes/{rid}/{revision_id}.md';temporary=f'notes/{rid}/.pending-{revision_id}'
        data=body.encode('utf-8');checksum=digest(data);instant=utc_now()
        with connection(self.database,write=True) as con:
            con.execute(file_operations.insert().values(id=operation_id,resource_id=rid,operation_type='note_revision',
                temp_path=temporary,final_path=path,new_hash=checksum,state='prepared',created_at=instant,updated_at=instant))
        self.files.write_revision(path,temporary,data)
        return operation_id,revision_id,path,checksum

    def _commit_revision(self,con,rid,data,version,prepared):
        op_id,rev_id,path,checksum=prepared
        self.files.read(path,checksum)
        con.execute(revisions.insert().values(id=rev_id,note_id=rid,version=version,body_path=path,content_sha256=checksum,
            title=data['title'],note_kind=data['note_kind'],local_date=data['local_date'],created_at=utc_now()))
        con.execute(update(file_operations).where(file_operations.c.id==op_id).values(state='committed',updated_at=utc_now()))

    def create(self,payload:NoteCreate,key):
        data=payload.model_dump(mode='json')
        with file_lock(self.root):
            with connection(self.database) as con:
                prior=self._prior(con,'note.create',key,data)
                if prior:return prior,True
            rid,instant=str(uuid.uuid4()),utc_now()
            prepared=self._prepare(rid,data['body']);_,_,path,checksum=prepared
            with connection(self.database,write=True) as con:
                # Global operation key may concurrently have been used by another domain.
                prior=self._prior(con,'note.create',key,data)
                if prior:return prior,True
                con.execute(resources.insert().values(id=rid,kind='note',title=data['title'],summary=None,privacy_level='personal',
                    created_at=instant,updated_at=instant,deleted_at=None,version=1))
                con.execute(notes.insert().values(resource_id=rid,note_kind=data['note_kind'],local_date=data['local_date'],
                    body_path=path,content_sha256=checksum,format='markdown'))
                self._commit_revision(con,rid,data,1,prepared)
                activity(con,rid,'created',['title','body','note_kind','local_date'])
                record(con,'note.create',key,data,{'resource_id':rid})
                return self._get(con,rid),False

    def _check_edit(self,con,rid,data):
        current=self._get(con,rid)
        if current['version']!=data['expected_version']:raise HubError('VERSION_CONFLICT')
        if current['content_sha256']!=data['expected_hash']:raise HubError('NOTE_HASH_CONFLICT')
        return current

    def change(self,rid,payload:NoteEdit,key):
        data=payload.model_dump(mode='json');operation='note.edit:'+rid
        with file_lock(self.root):
            with connection(self.database) as con:
                prior=self._prior(con,operation,key,data)
                if prior:return prior,True
                current=self._check_edit(con,rid,data)
            prepared=self._prepare(rid,data['body'])
            with connection(self.database,write=True) as con:
                prior=self._prior(con,operation,key,data)
                if prior:return prior,True
                self._check_edit(con,rid,data)
                version=current['version']+1
                con.execute(update(resources).where(resources.c.id==rid).values(title=data['title'],version=version,updated_at=utc_now()))
                con.execute(update(notes).where(notes.c.resource_id==rid).values(note_kind=data['note_kind'],local_date=data['local_date'],
                    body_path=prepared[2],content_sha256=prepared[3]))
                self._commit_revision(con,rid,data,version,prepared)
                activity(con,rid,'updated',['title','body','note_kind','local_date'])
                record(con,operation,key,data,{'resource_id':rid})
                return self._get(con,rid),False

    def trash(self,rid,expected,restore=False):
        with file_lock(self.root),connection(self.database,write=True) as con:
            current=self._get(con,rid,deleted=True,body=False)
            if current['version']!=expected:raise HubError('VERSION_CONFLICT')
            if bool(current['deleted_at'])!=restore:raise HubError('NOTE_STATE_CONFLICT')
            con.execute(update(resources).where(resources.c.id==rid).values(deleted_at=None if restore else utc_now(),
                updated_at=utc_now(),version=expected+1))
            activity(con,rid,'restored' if restore else 'trashed',['deleted_at'])
            return self._get(con,rid,deleted=True,body=False)

    def history(self,rid,limit=20,offset=0):
        with connection(self.database) as con:
            self._get(con,rid,body=False)
            total=con.execute(select(func.count()).select_from(revisions).where(revisions.c.note_id==rid)).scalar_one()
            rows=con.execute(select(revisions).where(revisions.c.note_id==rid).order_by(revisions.c.version.desc()).limit(limit).offset(offset)).mappings()
            return {'items':[dict(r) for r in rows],'total':total,'limit':limit,'offset':offset}

    def revision(self,rid,revision_id):
        with connection(self.database) as con:
            self._get(con,rid,body=False)
            row=con.execute(select(revisions).where(revisions.c.note_id==rid,revisions.c.id==revision_id)).mappings().first()
            if row is None:raise HubError('NOTE_NOT_FOUND',404)
            return {**row,'body':self.files.body(row['body_path'],row['content_sha256'])}

    def markdown(self,rid):
        row=self.get(rid)
        return row['body'].encode('utf-8')

    def export(self):
        with file_lock(self.root),connection(self.database) as con:
            rows=con.execute(select(*COLUMNS).select_from(JOIN).where(VISIBLE).order_by(resources.c.id)).mappings().all()
            history=con.execute(select(revisions).join(resources,revisions.c.note_id==resources.c.id).where(VISIBLE).order_by(revisions.c.note_id,revisions.c.version)).mappings().all()
            logs=con.execute(select(activities).join(resources,activities.c.resource_id==resources.c.id).where(VISIBLE,activities.c.privacy_level=='personal')).mappings().all()
            assigned=tag_map(con,[r['id'] for r in rows])
            manifest={'format':'personal-hub-note-export','format_version':1,'schema_revision':SCHEMA_REVISION,'created_at':utc_now(),
                'includes_trash':True,'excludes':['private','secrets','uncommitted_files'],
                'notes':[{**dict(r),'tags':assigned[r['id']]} for r in rows],'revisions':[dict(r) for r in history],'activities':[dict(r) for r in logs]}
            output=BytesIO();total=0
            with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED) as bundle:
                for row in history:
                    data=self.files.read(row['body_path'],row['content_sha256']);total+=len(data)
                    if total>64*1024*1024:raise HubError('NOTE_EXPORT_TOO_LARGE',413)
                    bundle.writestr(row['body_path'],data)
                bundle.writestr('notes.json',json.dumps(manifest,ensure_ascii=False,indent=2))
            return output.getvalue()

    def diagnose(self,reconcile=False):
        """Explicit maintenance only; no deletes or inferred business commits."""
        with file_lock(self.root),connection(self.database,write=reconcile) as con:
            rows=con.execute(select(file_operations).where(file_operations.c.state=='prepared')).mappings().all()
            counts={'prepared':len(rows),'orphaned':0,'conflict':0}
            for row in rows:
                reference=con.execute(select(revisions.c.id).where(revisions.c.body_path==row['final_path'])).first()
                state='conflict' if reference else 'orphaned'
                for path in [row['temp_path'],row['final_path']]:
                    try:self.files.read(path,row['new_hash'])
                    except HubError as error:
                        if error.code!='NOTE_FILE_MISSING':state='conflict'
                counts[state]+=1
                if reconcile:con.execute(update(file_operations).where(file_operations.c.id==row['id']).values(state=state,updated_at=utc_now()))
            issues=0
            for row in con.execute(select(revisions.c.body_path,revisions.c.content_sha256)):
                try:self.files.body(row.body_path,row.content_sha256)
                except HubError:issues+=1
            counts['revision_issues']=issues
            counts['action']='marked_only_files_preserved' if reconcile else 'read_only'
            return counts
