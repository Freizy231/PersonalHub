"""Ordinary resource navigation. No vault identity or filesystem path is accepted."""
from uuid import uuid4
from ...core.database import connection,HubError,utc_now
from ...core.operations import activity,record,replay

EXTENSIONS={'task':'tasks','event':'events','note':'notes','collection':'collections',
            'document':'documents','book':'books','bookmark':'web_bookmarks','course':'courses',
            'learning_goal':'learning_goals','learning_path':'learning_paths','knowledge_node':'knowledge_nodes','project':'projects','recipe':'recipes'}

def lineage_sql(alias='resources',trash=False):
    # UNION terminates cycles; extension foreign keys are followed without exposing their IDs.
    return f"""NOT EXISTS (WITH RECURSIVE lineage(id) AS (
        SELECT {alias}.id
        UNION SELECT n.parent_id FROM knowledge_nodes n JOIN lineage l ON n.resource_id=l.id WHERE n.parent_id IS NOT NULL
        UNION SELECT n.path_id FROM knowledge_nodes n JOIN lineage l ON n.resource_id=l.id
        UNION SELECT p.goal_id FROM learning_paths p JOIN lineage l ON p.resource_id=l.id WHERE p.goal_id IS NOT NULL
    ) SELECT 1 FROM lineage l LEFT JOIN resources auth ON auth.id=l.id
      WHERE auth.id IS NULL OR auth.privacy_level<>'personal' OR (auth.deleted_at IS NOT NULL AND (auth.id<>{alias}.id OR {0 if trash else 1}=1)))"""

def rows(con,sql,args=()):return [dict(r) for r in con.exec_driver_sql(sql,args).mappings()]

def visible(con,rid,*,trash=False):
    result=rows(con,'SELECT * FROM resources WHERE id=? AND privacy_level=\'personal\''+('' if trash else ' AND deleted_at IS NULL'),(rid,))
    if not result:raise HubError('RESOURCE_NOT_FOUND',404)
    if not con.exec_driver_sql('SELECT 1 FROM resources WHERE id=? AND '+lineage_sql(trash=trash),(rid,)).first():raise HubError('RESOURCE_NOT_FOUND',404)
    r=result[0];table=EXTENSIONS.get(r['kind'])
    if table is None or not con.exec_driver_sql(f'SELECT 1 FROM {table} WHERE resource_id=?',(rid,)).first():raise HubError('RESOURCE_NOT_FOUND',404)
    return r

def versioned(con,rid,version,*,trash=False):
    r=visible(con,rid,trash=trash)
    if r['version']!=version:raise HubError('VERSION_CONFLICT')
    return r

def bump(con,rid):
    con.exec_driver_sql('UPDATE resources SET version=version+1,updated_at=? WHERE id=?',(utc_now(),rid))

def create_resource(con,kind,title):
    rid,now=str(uuid4()),utc_now()
    con.exec_driver_sql("INSERT INTO resources(id,kind,title,privacy_level,created_at,updated_at,version) VALUES (?,?,?,'personal',?,?,1)",(rid,kind,title,now,now))
    return rid

class KnowledgeService:
    def __init__(self,database):self.database=database
    def catalog(self,q='',kind='',limit=50,offset=0):
        with connection(self.database) as con:
            clauses=lineage_sql()+" AND privacy_level='personal' AND deleted_at IS NULL AND instr(lower(title),lower(?))>0";args=[q]
            if kind:clauses+=' AND kind=?';args.append(kind)
            total=con.exec_driver_sql(f'SELECT count(*) FROM resources WHERE {clauses}',tuple(args)).scalar_one()
            found=rows(con,f'SELECT id,kind,title,version FROM resources WHERE {clauses} ORDER BY updated_at DESC,id LIMIT ? OFFSET ?',tuple(args+[limit,offset]))
            # Validate extension existence, no generic ghost resources.
            safe=[]
            for r in found:
                try:visible(con,r['id']);safe.append(r)
                except HubError:pass
            return {'items':safe,'total':total,'limit':limit,'offset':offset}
    def get(self,rid):
        with connection(self.database) as con:
            result=visible(con,rid)
            result['url']='hub://resource/'+rid
            return result
    def context(self,rid):
        with connection(self.database) as con:
            visible(con,rid)
            edges=rows(con,"""SELECT e.id,e.source_id,e.target_id,e.relation_type,r.id AS resource_id,r.kind,r.title,r.version
                FROM resource_relations e JOIN resources r ON r.id=CASE WHEN e.source_id=? THEN e.target_id ELSE e.source_id END
                WHERE (e.source_id=? OR e.target_id=?) AND r.privacy_level='personal' AND r.deleted_at IS NULL ORDER BY e.created_at,e.id LIMIT 500""",(rid,rid,rid))
            if visible(con,rid)['kind']=='knowledge_node':
                edges+=rows(con,"SELECT l.id,l.node_id AS source_id,l.task_id AS target_id,'practice' AS relation_type,r.id AS resource_id,r.kind,r.title,r.version FROM learning_node_tasks l JOIN resources r ON r.id=l.task_id WHERE l.node_id=? AND r.privacy_level='personal' AND r.deleted_at IS NULL",(rid,))
            if visible(con,rid)['kind']=='task':
                edges+=rows(con,"SELECT l.id,l.node_id AS source_id,l.task_id AS target_id,'practice' AS relation_type,r.id AS resource_id,r.kind,r.title,r.version FROM learning_node_tasks l JOIN resources r ON r.id=l.node_id WHERE l.task_id=? AND r.privacy_level='personal' AND r.deleted_at IS NULL",(rid,))
            # Project task/log relations are projections of their single authority.
            for table, column, edge_type in [('project_tasks','task_id',"'project_task'"),('project_entries','note_id','p.entry_kind')]:
                edges+=rows(con,f"SELECT p.id,p.project_id AS source_id,p.{column} AS target_id,{edge_type} AS relation_type,r.id AS resource_id,r.kind,r.title,r.version FROM {table} p JOIN resources r ON r.id=CASE WHEN p.project_id=? THEN p.{column} ELSE p.project_id END WHERE (p.project_id=? OR p.{column}=?) AND r.privacy_level='personal' AND r.deleted_at IS NULL ORDER BY p.created_at,p.id LIMIT 500",(rid,rid,rid))
            safe_edges=[]
            for e in edges:
                try:visible(con,e['resource_id']);safe_edges.append(e)
                except HubError:pass
            edges=safe_edges
            backlinks=rows(con,"""SELECT r.id,r.title,r.kind FROM note_links l JOIN resources r ON r.id=l.source_id JOIN notes n ON n.resource_id=r.id
                WHERE l.target_id=? AND r.privacy_level='personal' AND r.deleted_at IS NULL AND l.source_hash=n.content_sha256 ORDER BY r.updated_at DESC LIMIT 100""",(rid,))
            # Current on-disk hash is checked before returning a backlink.
            from ...core.note_files import NoteFiles
            from pathlib import Path
            safe=[]
            for n in backlinks:
                ref=con.exec_driver_sql('SELECT body_path,content_sha256 FROM notes WHERE resource_id=?',(n['id'],)).first()
                try:NoteFiles(Path(self.database).parent.parent).body(*ref);safe.append(n)
                except HubError:pass
            outgoing=[]
            if visible(con,rid)['kind']=='note':
                current=con.exec_driver_sql('SELECT body_path,content_sha256 FROM notes WHERE resource_id=?',(rid,)).first()
                try:
                    NoteFiles(Path(self.database).parent.parent).body(*current)
                    for link in rows(con,'SELECT target_id FROM note_links WHERE source_id=? AND source_hash=?',(rid,current[1])):
                        try:outgoing.append(visible(con,link['target_id']))
                        except HubError:pass
                except HubError:pass
            return {'resource':visible(con,rid),'relations':edges,'backlinks':safe,'outgoing':outgoing}
    def relate(self,rid,payload,key):
        data=payload.model_dump(mode='json');kind='resource.relate:'+rid
        with connection(self.database,write=True) as con:
            source=visible(con,rid);target=visible(con,str(payload.target_id))
            if replay(con,kind,key,data) is None:
                versioned(con,rid,payload.expected_version)
                if source['kind']=='project':
                    from ..projects.service import ProjectService
                    ProjectService(self.database)._editable(con,rid)
                if rid==str(payload.target_id):raise HubError('RELATION_INVALID',422)
                if payload.relation_type=='practice':
                    if source['kind']!='knowledge_node' or target['kind']!='task':raise HubError('RELATION_INVALID',422)
                    con.exec_driver_sql('INSERT OR IGNORE INTO learning_node_tasks VALUES(?,?,?,?)',(str(uuid4()),rid,str(payload.target_id),utc_now()))
                else:
                    if payload.relation_type=='material' and target['kind'] not in ('note','document','book','bookmark','course','collection'):raise HubError('RELATION_INVALID',422)
                    if payload.relation_type=='output' and target['kind'] not in ('note','document','book'):raise HubError('RELATION_INVALID',422)
                    con.exec_driver_sql('INSERT OR IGNORE INTO resource_relations(id,source_id,target_id,relation_type,created_at) VALUES(?,?,?,?,?)',
                        (str(uuid4()),rid,str(payload.target_id),payload.relation_type,utc_now()))
                bump(con,rid);activity(con,rid,'related',['relation_type']);record(con,kind,key,data,{'resource_id':rid})
        return self.context(rid)
    def unlink(self,rid,eid,version):
        with connection(self.database) as con:
            source=visible(con,rid)
            project_edge=source['kind']=='project' and any(con.exec_driver_sql('SELECT 1 FROM '+table+' WHERE id=? AND project_id=?',(eid,rid)).first() for table in ('project_tasks','project_entries'))
        if project_edge:
            from ..projects.service import ProjectService
            ProjectService(self.database).unlink(rid,eid,version)
            return self.context(rid)
        with connection(self.database,write=True) as con:
            if source['kind']=='project':
                from ..projects.service import ProjectService
                ProjectService(self.database)._editable(con,rid)

            versioned(con,rid,version)
            e=con.exec_driver_sql('SELECT target_id FROM resource_relations WHERE id=? AND source_id=?',(eid,rid)).first()
            if not e:
                e=con.exec_driver_sql('SELECT task_id FROM learning_node_tasks WHERE id=? AND node_id=?',(eid,rid)).first()
                if not e:raise HubError('RELATION_NOT_FOUND',404)
                visible(con,e[0]);con.exec_driver_sql('DELETE FROM learning_node_tasks WHERE id=?',(eid,))
            else:
                visible(con,e[0]);con.exec_driver_sql('DELETE FROM resource_relations WHERE id=?',(eid,))
            bump(con,rid)
            activity(con,rid,'unrelated',['relation_type'])
        return self.context(rid)
    def collections(self,trash=False,limit=100,offset=0):
        scope="WHERE r.privacy_level='personal' AND r.deleted_at IS "+('NOT NULL' if trash else 'NULL')
        with connection(self.database) as con:
            total=con.exec_driver_sql('SELECT count(*) FROM resources r JOIN collections c ON c.resource_id=r.id '+scope).scalar_one()
            items=rows(con,'SELECT r.*,c.description FROM resources r JOIN collections c ON c.resource_id=r.id '+scope+' ORDER BY r.updated_at DESC,r.id LIMIT ? OFFSET ?',(limit,offset))
            return {'items':items,'total':total,'limit':limit,'offset':offset}
    def collection(self,rid):
        with connection(self.database) as con:
            r=visible(con,rid)
            if r['kind']!='collection':raise HubError('COLLECTION_NOT_FOUND',404)
            r['description']=con.exec_driver_sql('SELECT description FROM collections WHERE resource_id=?',(rid,)).scalar_one()
            r['members']=rows(con,"SELECT r.id,r.title,r.kind,m.position FROM collection_members m JOIN resources r ON r.id=m.resource_id WHERE m.collection_id=? AND r.privacy_level='personal' AND r.deleted_at IS NULL ORDER BY m.position,r.id LIMIT 1000",(rid,))
            safe=[]
            for m in r['members']:
                try:visible(con,m['id']);safe.append(m)
                except HubError:pass
            r['members']=safe
            return r
    def save_collection(self,payload,key,rid=None):
        data=payload.model_dump(mode='json');kind='collection.save:'+str(rid)
        with connection(self.database,write=True) as con:
            if rid:visible(con,rid)
            prior=replay(con,kind,key,data)
            if prior:rid=prior['resource_id']
            else:
                if rid:
                    r=versioned(con,rid,payload.expected_version)
                    if r['kind']!='collection':raise HubError('COLLECTION_NOT_FOUND',404)
                    con.exec_driver_sql('UPDATE resources SET title=? WHERE id=?',(payload.title,rid));bump(con,rid)
                    con.exec_driver_sql('UPDATE collections SET description=? WHERE resource_id=?',(payload.description,rid))
                else:
                    rid=create_resource(con,'collection',payload.title)
                    con.exec_driver_sql('INSERT INTO collections(resource_id,description) VALUES(?,?)',(rid,payload.description))
                # Hidden/deleted members cannot be modified or silently removed.
                old=rows(con,'SELECT resource_id FROM collection_members WHERE collection_id=?',(rid,))
                hidden=[]
                for member in old:
                    try:visible(con,member['resource_id'])
                    except HubError:hidden.append(member['resource_id'])
                ids=[str(x) for x in payload.members]
                if rid in ids or len(set(ids))!=len(ids):raise HubError('COLLECTION_MEMBERS_INVALID',422)
                for mid in ids:
                    if visible(con,mid)['kind']=='collection':raise HubError('COLLECTION_NESTING_UNSUPPORTED',422)
                con.exec_driver_sql('DELETE FROM collection_members WHERE collection_id=?',(rid,))
                for i,mid in enumerate(ids+hidden):
                    con.exec_driver_sql('INSERT INTO collection_members VALUES(?,?,?,?)',(rid,mid,i,utc_now()))
                activity(con,rid,'saved',['title','members']);record(con,kind,key,data,{'resource_id':rid})
        return self.collection(rid)
    def trash_collection(self,rid,version,restore=False):
        with connection(self.database,write=True) as con:
            r=versioned(con,rid,version,trash=True)
            if r['kind']!='collection' or bool(r['deleted_at'])!=restore:raise HubError('COLLECTION_STATE_CONFLICT')
            con.exec_driver_sql('UPDATE resources SET deleted_at=? WHERE id=?',(None if restore else utc_now(),rid));bump(con,rid)
            activity(con,rid,'restored' if restore else 'trashed',['deleted_at'])
        return {'status':'restored' if restore else 'trashed'}
