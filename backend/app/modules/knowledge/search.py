"""Rebuildable ordinary FTS. Every hit rechecks privacy, version and live source hash."""
import re
import hashlib
from pathlib import Path
from ...core.database import connection,HubError,utc_now
from ...core.note_files import NoteFiles
from .service import rows,visible
LINK=re.compile(r'hub://resource/([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})(?![0-9a-fA-F-])')

class SearchService:
    def __init__(self,database):self.database=database;self.root=Path(database).parent.parent
    def source(self,con,r):
        rid=r['id'];kind=r['kind'];body='';source_hash=''
        if kind=='note':
            n=con.exec_driver_sql('SELECT body_path,content_sha256 FROM notes WHERE resource_id=?',(rid,)).first()
            if not n:raise HubError('RESOURCE_NOT_FOUND',404)
            body=NoteFiles(self.root).body(*n);source_hash=n[1]
        elif kind=='task':body=con.exec_driver_sql('SELECT coalesce(description,\'\') FROM tasks WHERE resource_id=?',(rid,)).scalar_one()
        elif kind=='event':body=con.exec_driver_sql('SELECT coalesce(description,\'\') FROM events WHERE resource_id=?',(rid,)).scalar_one()
        elif kind=='collection':body=con.exec_driver_sql('SELECT description FROM collections WHERE resource_id=?',(rid,)).scalar_one()
        elif kind=='document':
            from ..library.documents.storage import BlobFiles
            d=con.exec_driver_sql('SELECT extracted_text_path,text_sha256,abstract,authors FROM documents WHERE resource_id=?',(rid,)).first()
            from ...core.file_integrity import verify
            original=con.exec_driver_sql('SELECT b.relative_path,b.sha256 FROM documents d JOIN blobs b ON b.id=d.blob_id WHERE d.resource_id=?',(rid,)).first()
            verify(BlobFiles(self.root),*original)
            body=' '.join(x or '' for x in d[2:])
            if d[0]:body+='\n'+BlobFiles(self.root).read(d[0],d[1]).decode('utf-8')
        elif kind in ('book','bookmark','course','learning_goal','learning_path','knowledge_node','project','recipe'):
            from .service import EXTENSIONS
            row=rows(con,f'SELECT * FROM {EXTENSIONS[kind]} WHERE resource_id=?',(rid,))[0]
            body=' '.join(str(v) for k,v in row.items() if k not in ('resource_id','parent_id','path_id','goal_id','mastery','local_directory','last_opened_at') and v is not None)
        return body,source_hash or hashlib.sha256((r['title']+'\n'+body).encode()).hexdigest()
    def rebuild(self):
        # Explicit user action/maintenance only. No network or startup migration.
        built=[];skipped=0;links=[]
        with connection(self.database) as con:
            for r in rows(con,"SELECT * FROM resources WHERE privacy_level='personal' AND deleted_at IS NULL ORDER BY id"):
                try:
                    visible(con,r['id']);body,digest=self.source(con,r)
                    built.append((r,body,digest))
                    if r['kind']=='note':
                        for target in set(LINK.findall(body)):
                            try:visible(con,target.lower());links.append((r['id'],target.lower(),digest,utc_now()))
                            except HubError:pass
                except (HubError,UnicodeError):skipped+=1
        with connection(self.database,write=True) as con:
            con.exec_driver_sql('DELETE FROM search_fts');con.exec_driver_sql('DELETE FROM search_entries');con.exec_driver_sql('DELETE FROM note_links')
            accepted=0
            for r,body,digest in built:
                try:
                    current=visible(con,r['id'])
                    if current['version']!=r['version'] or self.source(con,current)[1]!=digest:skipped+=1;continue
                except HubError:skipped+=1;continue
                con.exec_driver_sql('INSERT INTO search_entries VALUES(?,?,?,?,?,?)',(r['id'],r['version'],digest,r['title'],body,utc_now()))
                con.exec_driver_sql('INSERT INTO search_fts(resource_id,title,body) VALUES(?,?,?)',(r['id'],r['title'],body));accepted+=1
            for edge in links:
                if con.exec_driver_sql('SELECT 1 FROM search_entries WHERE resource_id=? AND source_hash=?',(edge[0],edge[2])).first():
                    con.exec_driver_sql('INSERT INTO note_links VALUES(?,?,?,?)',edge)
        return {'indexed':accepted,'skipped':skipped,'rebuilt_at':utc_now()}
    def query(self,q,kind='',limit=20,offset=0):
        from .service import lineage_sql,EXTENSIONS
        from ...core.file_integrity import verify
        from ..library.documents.storage import BlobFiles
        q=q.strip()
        if not q:return {'items':[],'total':0,'limit':limit,'offset':offset,'strategy':'empty'}
        strategy='fts5_trigram' if len(q)>=3 else 'literal_short_scan'
        with connection(self.database) as con:
            scope="r.privacy_level='personal' AND r.deleted_at IS NULL AND r.version=e.resource_version AND (?='' OR r.kind=?) AND "+lineage_sql('r')
            extensions=' OR '.join("(r.kind='"+k+"' AND EXISTS(SELECT 1 FROM "+t+" WHERE resource_id=r.id))" for k,t in EXTENSIONS.items())
            scope+=' AND ('+extensions+')'
            # Body text is projected down to a 300-character window in SQL. Pulling full
            # documents here made a single search allocate the whole corpus.
            columns="e.resource_id,substr(e.body,max(1,instr(lower(e.body),lower(?))-60),300) AS snippet,e.source_hash,r.id,r.kind,r.title"
            if len(q)>=3:
                escaped='"'+q.replace('"','""')+'"'
                candidates=rows(con,'SELECT '+columns+' FROM search_fts f JOIN search_entries e ON e.resource_id=f.resource_id JOIN resources r ON r.id=e.resource_id WHERE search_fts MATCH ? AND '+scope+' ORDER BY rank LIMIT 10001',(q,escaped,kind,kind))
            else:
                candidates=rows(con,'SELECT '+columns+' FROM search_entries e JOIN resources r ON r.id=e.resource_id WHERE '+scope+' AND (instr(lower(e.title),lower(?))>0 OR instr(lower(e.body),lower(?))>0) ORDER BY r.updated_at DESC,r.id LIMIT 10001',(q,kind,kind,q,q))
            file_ids=[r['id'] for r in candidates if r['kind'] in ('note','document')];refs={}
            for start in range(0,len(file_ids),500):
                chunk=file_ids[start:start+500];marks=','.join('?' for _ in chunk)
                for n in rows(con,'SELECT resource_id,body_path,content_sha256 FROM notes WHERE resource_id IN ('+marks+')',tuple(chunk)):refs[n['resource_id']]=n
                for d in rows(con,'SELECT d.resource_id,d.extracted_text_path,d.text_sha256,b.relative_path,b.sha256 FROM documents d JOIN blobs b ON b.id=d.blob_id WHERE d.resource_id IN ('+marks+')',tuple(chunk)):refs[d['resource_id']]=d
            found=[]
            for r in candidates:
                try:
                    if r['kind']=='note':
                        ref=refs[r['id']]
                        if ref['content_sha256']!=r['source_hash']:continue
                        verify(NoteFiles(self.root),ref['body_path'],ref['content_sha256'])
                    elif r['kind']=='document':
                        ref=refs[r['id']];store=BlobFiles(self.root)
                        verify(store,ref['relative_path'],ref['sha256'])
                        if ref['extracted_text_path']:verify(store,ref['extracted_text_path'],ref['text_sha256'])
                except (HubError,KeyError):continue
                found.append({'id':r['id'],'kind':r['kind'],'title':r['title'],'snippet':r['snippet'] or '','url':'hub://resource/'+r['id']})
            return {'items':found[offset:offset+limit],'total':len(found),'limit':limit,'offset':offset,'strategy':strategy,'truncated':len(candidates)>10000,'stale_policy':'changed_resources_require_refresh'}
    def refresh(self,limit=200):
        """Incremental bounded projection; original facts/files stay authoritative."""
        from .service import lineage_sql
        built=[];skipped=0
        with connection(self.database) as c:
            candidates=rows(c,"SELECT r.* FROM resources r LEFT JOIN search_entries e ON e.resource_id=r.id WHERE r.privacy_level='personal' AND r.deleted_at IS NULL AND (e.resource_id IS NULL OR e.resource_version<>r.version) AND "+lineage_sql('r')+' ORDER BY r.updated_at,r.id LIMIT ?',(limit,))
            for r in candidates:
                try:visible(c,r['id']);body,digest=self.source(c,r);built.append((r,body,digest))
                except (HubError,UnicodeError):skipped+=1
            stale=[r['resource_id'] for r in rows(c,"SELECT e.resource_id FROM search_entries e JOIN resources r ON r.id=e.resource_id WHERE r.privacy_level<>'personal' OR r.deleted_at IS NOT NULL OR NOT "+lineage_sql('r'))]
        if not built and not stale:return {'indexed':0,'skipped':skipped}
        with connection(self.database,write=True) as c:
            for rid in stale:
                c.exec_driver_sql('DELETE FROM search_fts WHERE resource_id=?',(rid,));c.exec_driver_sql('DELETE FROM search_entries WHERE resource_id=?',(rid,));c.exec_driver_sql('DELETE FROM note_links WHERE source_id=?',(rid,))
            indexed=0
            for r,body,digest in built:
                try:current=visible(c,r['id'])
                except HubError:continue
                if current['version']!=r['version']:continue
                c.exec_driver_sql('DELETE FROM search_fts WHERE resource_id=?',(r['id'],))
                c.exec_driver_sql('INSERT INTO search_entries VALUES(?,?,?,?,?,?) ON CONFLICT(resource_id) DO UPDATE SET resource_version=excluded.resource_version,source_hash=excluded.source_hash,title=excluded.title,body=excluded.body,indexed_at=excluded.indexed_at',(r['id'],r['version'],digest,r['title'],body,utc_now()))
                c.exec_driver_sql('INSERT INTO search_fts(resource_id,title,body) VALUES(?,?,?)',(r['id'],r['title'],body))
                c.exec_driver_sql('DELETE FROM note_links WHERE source_id=?',(r['id'],))
                if r['kind']=='note':
                    for target in set(LINK.findall(body)):
                        try:visible(c,target.lower())
                        except HubError:continue
                        c.exec_driver_sql('INSERT INTO note_links VALUES(?,?,?,?)',(r['id'],target.lower(),digest,utc_now()))
                indexed+=1
        return {'indexed':indexed,'skipped':skipped}
