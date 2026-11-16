"""Evidence-based mastery. Duration is a record, never a mastery transition."""
import hashlib
from uuid import uuid4,uuid5,NAMESPACE_URL
from datetime import date,timedelta
from ...core.database import connection,HubError,utc_now
from ...core.operations import activity,record,replay
from ..knowledge.service import visible,versioned,bump,create_resource,rows
from ..knowledge.search import SearchService
from ..library.notes.service import NoteService
from ..library.notes.schemas import NoteCreate
from ..life.tasks.models import tasks
TABLES={'learning_goal':'learning_goals','learning_path':'learning_paths','knowledge_node':'knowledge_nodes'}
class LearningService:
 def __init__(self,database):self.database=database
 def _get(self,c,kind,rid,trash=False):
  r=visible(c,rid,trash=trash)
  if r['kind']!=kind:raise HubError('RESOURCE_NOT_FOUND',404)
  r.update(rows(c,'SELECT * FROM '+TABLES[kind]+' WHERE resource_id=?',(rid,))[0]);r.pop('resource_id',None)
  if kind=='learning_path' and r['goal_id']:visible(c,r['goal_id'])
  if kind=='knowledge_node':
   path=self._get(c,'learning_path',r['path_id']);parent=r['parent_id'];seen={rid}
   while parent:
    if parent in seen:raise HubError('LEARNING_TREE_INVALID')
    seen.add(parent);visible(c,parent)
    p=c.exec_driver_sql('SELECT path_id,parent_id FROM knowledge_nodes WHERE resource_id=?',(parent,)).first()
    if not p or p[0]!=r['path_id']:raise HubError('LEARNING_TREE_INVALID')
    parent=p[1]
   latest=c.exec_driver_sql('SELECT id FROM learning_assessments WHERE node_id=? ORDER BY created_at DESC,id DESC LIMIT 1',(rid,)).first()
   if latest:
    for evidence in rows(c,'SELECT resource_id FROM assessment_evidence WHERE assessment_id=?',(latest[0],)):
     try:visible(c,evidence['resource_id'])
     except HubError:r['mastery']='unavailable';break
  return r
 def get(self,kind,rid):
  with connection(self.database) as c:return self._get(c,kind,rid)
 def list(self,kind,trash=False,limit=50,offset=0):
  with connection(self.database) as c:
   found=rows(c,"SELECT r.id FROM resources r JOIN "+TABLES[kind]+" x ON x.resource_id=r.id WHERE r.privacy_level='personal' AND r.deleted_at IS "+('NOT NULL' if trash else 'NULL')+' ORDER BY r.updated_at DESC,r.id')
   safe=[]
   for r in found:
    try:safe.append(self._get(c,kind,r['id'],trash))
    except HubError:pass
   return {'items':safe[offset:offset+limit],'total':len(safe),'limit':limit,'offset':offset}
 def save(self,kind,payload,key,rid=None):
  data=payload.model_dump(mode='json');operation='learning.save:'+kind+':'+str(rid)
  with connection(self.database,write=True) as c:
   if rid:self._get(c,kind,rid)
   prior=replay(c,operation,key,data)
   if prior:return self._get(c,kind,prior['resource_id'])
   fields={k:v for k,v in data.items() if k not in ('title','expected_version')}
   if kind=='learning_path' and fields['goal_id']:self._get(c,'learning_goal',fields['goal_id'])
   if kind=='knowledge_node':
    self._get(c,'learning_path',fields['path_id'])
    if rid:
     old=self._get(c,kind,rid)
     if old['path_id']!=fields['path_id']:raise HubError('LEARNING_PATH_MOVE_UNSUPPORTED',422)
    parent=fields['parent_id'];seen={rid}
    while parent:
     if parent in seen:raise HubError('LEARNING_CYCLE',422)
     if len(seen)>64:raise HubError('LEARNING_TREE_TOO_DEEP',422)
     seen.add(parent);p=self._get(c,kind,parent)
     if p['path_id']!=fields['path_id']:raise HubError('LEARNING_PARENT_PATH_MISMATCH',422)
     parent=p['parent_id']
    # Reparenting a subtree must also preserve the depth budget for every descendant.
    if rid:
     descendants=rows(c,'SELECT resource_id,parent_id FROM knowledge_nodes WHERE path_id=?',(fields['path_id'],))
     children={}
     for child in descendants:children.setdefault(child['parent_id'],[]).append(child['resource_id'])
     todo=[(rid,len(seen)-1)];visited=set()
     while todo:
      child,depth=todo.pop()
      if child in visited:raise HubError('LEARNING_CYCLE',422)
      visited.add(child)
      if depth>64:raise HubError('LEARNING_TREE_TOO_DEEP',422)
      todo.extend((descendant,depth+1) for descendant in children.get(child,[]))
   table=TABLES[kind]
   if rid:
    versioned(c,rid,payload.expected_version);c.exec_driver_sql('UPDATE resources SET title=? WHERE id=?',(payload.title,rid));bump(c,rid)
    c.exec_driver_sql('UPDATE '+table+' SET '+','.join(k+'=?' for k in fields)+' WHERE resource_id=?',tuple(fields.values())+(rid,))
   else:
    rid=create_resource(c,kind,payload.title)
    c.exec_driver_sql('INSERT INTO '+table+'(resource_id,'+','.join(fields)+') VALUES('+','.join('?' for _ in range(len(fields)+1))+')',(rid,*fields.values()))
   activity(c,rid,'saved',fields.keys());record(c,operation,key,data,{'resource_id':rid});return self._get(c,kind,rid)
 def tree(self,pid):
  with connection(self.database) as c:
   path=self._get(c,'learning_path',pid);found=rows(c,'SELECT resource_id FROM knowledge_nodes WHERE path_id=? ORDER BY position,resource_id',(pid,));safe=[]
   for n in found:
    try:safe.append(self._get(c,'knowledge_node',n['resource_id']))
    except HubError:pass
   children={}
   for n in safe:children.setdefault(n['parent_id'],[]).append(n)
   ordered=[]
   def walk(parent,depth):
    for n in children.get(parent,[]):n['depth']=depth;ordered.append(n);walk(n['id'],depth+1)
   walk(None,0)
   return {'path':path,'nodes':ordered,'progress':{'verified':sum(n['mastery']=='verified' for n in safe),'total':len(safe),'denominator':'所有可见未删除知识点，父节点也独立验收；时间不计入掌握进度'}}
 def context(self,nid):
  with connection(self.database) as c:
   node=self._get(c,'knowledge_node',nid)
   found=rows(c,'SELECT * FROM learning_assessments WHERE node_id=? ORDER BY created_at DESC,id LIMIT 100',(nid,));assessments=[]
   for a in found:
    evidence=rows(c,'SELECT * FROM assessment_evidence WHERE assessment_id=? ORDER BY resource_id',(a['id'],));valid=True
    for e in evidence:
     try:
      r=visible(c,e['resource_id']);_,digest=SearchService(self.database).source(c,r)
      e['current_version']=r['version'];e['changed']=r['version']!=e['resource_version'] or digest!=e['source_hash']
     except HubError:valid=False;break
    if valid:a['evidence']=evidence;assessments.append(a)
   sessions=rows(c,'SELECT * FROM learning_sessions WHERE node_id=? ORDER BY started_at DESC LIMIT 100',(nid,))
   safe_sessions=[]
   for s in sessions:
    try:
     if s['reflection_note_id']:visible(c,s['reflection_note_id'])
     safe_sessions.append(s)
    except HubError:pass
   tasks_=rows(c,"SELECT r.id,r.title,r.version,t.status,t.planned_date FROM learning_node_tasks l JOIN resources r ON r.id=l.task_id JOIN tasks t ON t.resource_id=r.id WHERE l.node_id=? AND r.privacy_level='personal' AND r.deleted_at IS NULL ORDER BY r.created_at DESC LIMIT 100",(nid,))
   return {'node':node,'assessments':assessments,'sessions':safe_sessions,'tasks':tasks_}
 def assess(self,nid,payload,key):
  data=payload.model_dump(mode='json');operation='learning.assess:'+nid
  with connection(self.database,write=True) as c:
   node=self._get(c,'knowledge_node',nid)
   if replay(c,operation,key,data) is None:
    versioned(c,nid,payload.expected_version);evidence=[];qualified=False
    for eid in data['evidence']:
     r=visible(c,eid);body,digest=SearchService(self.database).source(c,r);etype='material'
     if r['kind']=='note' and body.strip():etype='output';qualified=True
     if r['kind']=='task':
      status=c.exec_driver_sql('SELECT status FROM tasks WHERE resource_id=?',(eid,)).scalar_one()
      if status=='done':etype='practice';qualified=True
     evidence.append((r,digest,etype))
    if payload.mastery=='verified' and (not node['acceptance_criteria'].strip() or not qualified):raise HubError('LEARNING_EVIDENCE_REQUIRED',422)
    aid=str(uuid4());now=utc_now()
    c.exec_driver_sql('INSERT INTO learning_assessments VALUES(?,?,?,?,?,?)',(aid,nid,payload.mastery,node['acceptance_criteria'],payload.conclusion,now))
    for r,digest,etype in evidence:c.exec_driver_sql('INSERT INTO assessment_evidence VALUES(?,?,?,?,?,?,?)',(aid,r['id'],r['version'],digest,r['title'],etype,now))
    c.exec_driver_sql('UPDATE knowledge_nodes SET mastery=? WHERE resource_id=?',(payload.mastery,nid));bump(c,nid)
    activity(c,nid,'assessed',['mastery','evidence']);record(c,operation,key,data,{'assessment_id':aid})
  return self.context(nid)
 def session(self,payload,key):
  data=payload.model_dump(mode='json');operation='learning.session';nid=str(payload.node_id)
  with connection(self.database,write=True) as c:
   self._get(c,'knowledge_node',nid)
   prior=replay(c,operation,key,data)
   if prior:return rows(c,'SELECT * FROM learning_sessions WHERE id=?',(prior['session_id'],))[0]
   if payload.reflection_note_id:
    if visible(c,str(payload.reflection_note_id))['kind']!='note':raise HubError('NOTE_NOT_FOUND',404)
   sid=str(uuid4());duration=int((payload.ended_at-payload.started_at).total_seconds())
   c.exec_driver_sql('INSERT INTO learning_sessions VALUES(?,?,?,?,?,?,?)',(sid,nid,data['started_at'],data['ended_at'],duration,data['reflection_note_id'],utc_now()))
   activity(c,nid,'studied',['duration_seconds']);record(c,operation,key,data,{'session_id':sid})
   return rows(c,'SELECT * FROM learning_sessions WHERE id=?',(sid,))[0]
 def practice(self,nid,payload,key):
  from ..life.tasks.schemas import TaskCreate
  validated=TaskCreate(title=payload.title,description=payload.description,planned_date=payload.planned_date.isoformat() if payload.planned_date else None)
  data=payload.model_dump(mode='json');operation='learning.practice:'+nid
  with connection(self.database,write=True) as c:
   self._get(c,'knowledge_node',nid)
   prior=replay(c,operation,key,data)
   if prior:return visible(c,prior['resource_id'])
   versioned(c,nid,payload.expected_version);tid=create_resource(c,'task',payload.title)
   values=validated.model_dump(mode='json');values.update(validated.values_for_storage());values.pop('title')
   c.execute(tasks.insert().values(resource_id=tid,**values,status='todo',completed_at=None,estimated_seconds=None,position=0))
   c.exec_driver_sql('INSERT INTO learning_node_tasks VALUES(?,?,?,?)',(str(uuid4()),nid,tid,utc_now()))
   bump(c,nid);activity(c,tid,'created',['learning_node']);record(c,operation,key,data,{'resource_id':tid});return visible(c,tid)
 def review(self,pid,payload,key):
  data=payload.model_dump(mode='json');operation='learning.review:'+pid
  with connection(self.database) as c:
   self._get(c,'learning_path',pid);prior=replay(c,operation,key,data)
   if prior:visible(c,prior['resource_id']);return prior
   versioned(c,pid,payload.expected_version)
  # Immutable Note adapter owns the file protocol. An interrupted final link may leave a recoverable ordinary Note;
  # same deterministic child key recovers it on retry, never silently overwrites body.
  child=str(uuid5(NAMESPACE_URL,'personal-hub-review:'+key))
  note=NoteService(self.database).create(NoteCreate(title=payload.title,body=payload.body,note_kind='knowledge'),child)[0]
  with connection(self.database,write=True) as c:
   self._get(c,'learning_path',pid);prior=replay(c,operation,key,data)
   if prior:return prior
   versioned(c,pid,payload.expected_version);rid=str(uuid4())
   c.exec_driver_sql('INSERT INTO learning_reviews VALUES(?,?,?,?,?,?)',(rid,pid,note['id'],data['period_start'],data['period_end'],utc_now()))
   bump(c,pid);activity(c,pid,'reviewed',['note']);record(c,operation,key,data,{'review_id':rid,'resource_id':note['id']})
   return {'review_id':rid,'resource_id':note['id']}
 def reviews(self,pid):
  with connection(self.database) as c:
   self._get(c,'learning_path',pid)
   return {'items':rows(c,"SELECT l.id,l.note_id,l.period_start,l.period_end,r.title FROM learning_reviews l JOIN resources r ON r.id=l.note_id WHERE l.path_id=? AND r.privacy_level='personal' AND r.deleted_at IS NULL ORDER BY l.created_at DESC LIMIT 100",(pid,))}
 def trash(self,kind,rid,version,restore=False):
  with connection(self.database,write=True) as c:
   r=self._get(c,kind,rid,True);versioned(c,rid,version,trash=True)
   if bool(r['deleted_at'])!=restore:raise HubError('RESOURCE_STATE_CONFLICT')
   if not restore:
    table,field=('learning_paths','goal_id') if kind=='learning_goal' else ('knowledge_nodes','path_id') if kind=='learning_path' else ('knowledge_nodes','parent_id')
    if c.exec_driver_sql(f'SELECT 1 FROM {table} x JOIN resources r ON r.id=x.resource_id WHERE x.{field}=? AND r.deleted_at IS NULL LIMIT 1',(rid,)).first():raise HubError('LEARNING_HAS_ACTIVE_CHILDREN')
   c.exec_driver_sql('UPDATE resources SET deleted_at=? WHERE id=?',(None if restore else utc_now(),rid));bump(c,rid)
   activity(c,rid,'restored' if restore else 'trashed',['deleted_at']);return self._get(c,kind,rid,True)
 def overview(self):
  today=date.today();week=today-timedelta(days=today.weekday())
  # Stored timestamps are UTC ("...Z"). The week starts at local midnight, so compare against
  # that instant; a bare date would put the boundary at UTC midnight and shift evening sessions.
  from datetime import datetime,timezone
  week_start=datetime.combine(week,datetime.min.time()).astimezone().astimezone(timezone.utc).isoformat(timespec='microseconds').replace('+00:00','Z')
  paths=self.list('learning_path',limit=100)['items'];trees=[self.tree(p['id']) for p in paths]
  with connection(self.database) as c:
   sessions=rows(c,"SELECT s.node_id,s.duration_seconds FROM learning_sessions s JOIN resources r ON r.id=s.node_id WHERE s.started_at>=? AND r.privacy_level='personal' AND r.deleted_at IS NULL",(week_start,))
   valid_nodes={n['id'] for t in trees for n in t['nodes']}
   assessments=rows(c,"SELECT a.id,a.node_id FROM learning_assessments a WHERE a.created_at>=? AND a.mastery='verified'",(week_start,))
   evidence_count=0
   for a in assessments:
    if a['node_id'] not in valid_nodes:continue
    if any(x['id']==a['id'] for x in self.context(a['node_id'])['assessments']):evidence_count+=1
  return {'paths':[{'id':t['path']['id'],'title':t['path']['title'],**t['progress']} for t in trees],
          'week_start':week.isoformat(),'study_seconds':sum(s['duration_seconds'] for s in sessions if s['node_id'] in valid_nodes),
          'verified_assessments_this_week':evidence_count,'mastery_policy':'学习时长不自动改变掌握程度'}
