"""Portable ordinary ZIP. Full encrypted disaster recovery remains stopped-writer snapshot."""
from io import BytesIO
import json,zipfile,hashlib
from pathlib import Path
import asyncio
from fastapi import APIRouter,Depends,Request
from fastapi.responses import Response
from ...core.security import require_session
from ...core.database import connection,HubError,SCHEMA_REVISION,utc_now
from ...core.note_files import NoteFiles,file_lock
from ..library.documents.storage import BlobFiles
from .service import EXTENSIONS,rows,visible
MAX_EXPORT=256*1024*1024

def portable_export(database):
 root=Path(database).parent.parent;files={};used=0
 def include(path,data):
  nonlocal used
  if path in files:return
  used+=len(data)
  if used>MAX_EXPORT:raise HubError('EXPORT_TOO_LARGE',413)
  files[path]=data
 with file_lock(root),connection(database) as c:
  rs=rows(c,"SELECT * FROM resources WHERE privacy_level='personal' ORDER BY id")
  safe=[]
  for r in rs:
   try:visible(c,r['id'],trash=True);safe.append(r)
   except HubError:pass
  rs=safe
  ids={r['id'] for r in rs};data={'format':'personal-hub-ordinary','format_version':1,'schema_revision':SCHEMA_REVISION,'created_at':utc_now(),'includes_trash':True,'tables':{'resources':rs}}
  tables=data['tables']
  for kind,table in EXTENSIONS.items():
   tables[table]=[r for r in rows(c,'SELECT * FROM '+table) if r['resource_id'] in ids]
  # Ordinary export must not reveal a hidden linked identity or derived private assessment state.
  for event in tables['events']:
   if event['task_id'] not in ids:event['task_id']=None
  for node in tables['knowledge_nodes']:
   latest=c.exec_driver_sql('SELECT id FROM learning_assessments WHERE node_id=? ORDER BY created_at DESC,id DESC LIMIT 1',(node['resource_id'],)).first()
   if latest and any(e['resource_id'] not in ids for e in rows(c,'SELECT resource_id FROM assessment_evidence WHERE assessment_id=?',(latest[0],))):node['mastery']='unavailable'
  for r in tables['notes']:
   include(r['body_path'],NoteFiles(root).read(r['body_path'],r['content_sha256']))
  tables['note_revisions']=[r for r in rows(c,'SELECT * FROM note_revisions') if r['note_id'] in ids]
  for r in tables['note_revisions']:include(r['body_path'],NoteFiles(root).read(r['body_path'],r['content_sha256']))
  bid={r['blob_id'] for r in tables['documents']};tables['blobs']=[r for r in rows(c,'SELECT * FROM blobs') if r['id'] in bid]
  for r in tables['blobs']:include(r['relative_path'],BlobFiles(root).read(r['relative_path'],r['sha256']))
  for r in tables['documents']:
   if r['extracted_text_path']:include(r['extracted_text_path'],BlobFiles(root).read(r['extracted_text_path'],r['text_sha256']))
  tables['resource_tags']=[r for r in rows(c,'SELECT * FROM resource_tags') if r['resource_id'] in ids];tids={r['tag_id'] for r in tables['resource_tags']}
  tables['tags']=[r for r in rows(c,'SELECT * FROM tags') if r['id'] in tids]
  for table,columns in [('collection_members',['collection_id','resource_id']),('resource_relations',['source_id','target_id']),('note_links',['source_id','target_id']),('document_bookmarks',['document_id']),('learning_node_tasks',['node_id','task_id']),('learning_reviews',['path_id','note_id'])]:
   tables[table]=[r for r in rows(c,'SELECT * FROM '+table) if all(r[col] in ids for col in columns)]
  tables['learning_sessions']=[r for r in rows(c,'SELECT * FROM learning_sessions') if r['node_id'] in ids and (not r['reflection_note_id'] or r['reflection_note_id'] in ids)]
  ev=rows(c,'SELECT * FROM assessment_evidence');hidden={r['assessment_id'] for r in ev if r['resource_id'] not in ids}
  tables['learning_assessments']=[r for r in rows(c,'SELECT * FROM learning_assessments') if r['node_id'] in ids and r['id'] not in hidden]
  aids={r['id'] for r in tables['learning_assessments']};tables['assessment_evidence']=[r for r in ev if r['assessment_id'] in aids]
  tables['project_milestones']=[r for r in rows(c,'SELECT * FROM project_milestones') if r['project_id'] in ids]
  mids={m['id'] for m in tables['project_milestones']}
  tables['project_tasks']=[r for r in rows(c,'SELECT * FROM project_tasks') if r['project_id'] in ids and r['task_id'] in ids and (r['milestone_id'] is None or r['milestone_id'] in mids)]
  tables['project_entries']=[r for r in rows(c,'SELECT * FROM project_entries') if r['project_id'] in ids and r['note_id'] in ids]
  tables['diet_ingredients']=rows(c,'SELECT * FROM diet_ingredients')
  tables['recipe_ingredients']=[r for r in rows(c,'SELECT * FROM recipe_ingredients') if r['recipe_id'] in ids]
  # A meal is exported when it is visible; its dishes then travel with it.
  # Free-text dishes expose no hidden identity, recipe dishes only survive when
  # the recipe itself is visible.
  meals=[r for r in rows(c,'SELECT * FROM meal_plans') if not r['recipe_id'] or r['recipe_id'] in ids]
  tables['meal_plans']=meals
  meal_ids={r['id'] for r in meals}
  tables['meal_dishes']=[r for r in rows(c,'SELECT * FROM meal_dishes')
                         if r['meal_id'] in meal_ids and (not r['recipe_id'] or r['recipe_id'] in ids)]
  for table in ('shopping_lists','shopping_items','inventory_balances'):
   tables[table]=rows(c,'SELECT * FROM '+table)
  from ..life.exercise.service import ExerciseService
  for table in ('exercise_plans','exercise_records'):
   tables[table]=[ExerciseService.safe(c,r) for r in rows(c,'SELECT * FROM '+table)]
  tables['reminders']=[r for r in rows(c,'SELECT * FROM reminders') if r['resource_id'] in ids]
  from ..dashboard.service import DashboardService
  preferences=DashboardService(database)._read(c)
  tables['dashboard_preferences']=[preferences] if preferences['state']!='invalid' else []
  tables['activities']=[r for r in rows(c,"SELECT * FROM activities WHERE privacy_level='personal'") if r['resource_id'] in ids]
  include('data.json',json.dumps(data,ensure_ascii=False,indent=2).encode())
  manifest={'format':'personal-hub-portable','format_version':1,'excludes':['private','vault','secrets','legacy_intelligence','search_index','inflight_files'],
            'files':{p:{'sha256':hashlib.sha256(b).hexdigest(),'size':len(b)} for p,b in files.items()}}
  output=BytesIO()
  with zipfile.ZipFile(output,'w',zipfile.ZIP_DEFLATED) as archive:
   for path,b in files.items():archive.writestr(path,b)
   archive.writestr('manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2))
  return output.getvalue()
router=APIRouter(prefix='/api/v1/exports',dependencies=[Depends(require_session)])
@router.get('/ordinary')
async def export(request:Request):
 data=await asyncio.to_thread(portable_export,request.app.state.settings.database)
 return Response(data,media_type='application/zip',headers={'Content-Disposition':'attachment; filename="personal-hub-ordinary.zip"'})
