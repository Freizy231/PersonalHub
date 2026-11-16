import asyncio
from typing import Literal
from uuid import UUID
from fastapi import APIRouter,Depends,Header,Query,Request
from fastapi.responses import JSONResponse,Response
from ....core.security import require_session
from ...life.tasks.schemas import TaskVersion
from .service import NoteService
from .schemas import NoteCreate,NoteEdit
router=APIRouter(prefix='/api/v1/notes',dependencies=[Depends(require_session)])

def service(request):
    settings=request.app.state.settings
    return NoteService(settings.database,settings.data_root)

@router.get('')
async def listing(request:Request,scope:Literal['all','quick','journal','knowledge','experiment','trash']='all',
    limit:int=Query(20,ge=1,le=100),offset:int=Query(0,ge=0,le=1000000),tag_id:UUID|None=None):
    return await asyncio.to_thread(service(request).list,scope,limit,offset,str(tag_id) if tag_id else None)

@router.get('/export')
async def export(request:Request):
    result=await asyncio.to_thread(service(request).export)
    return Response(result,media_type='application/zip',headers={'Content-Disposition':'attachment; filename="personal-hub-notes.zip"'})

@router.get('/{rid}')
async def get(rid:UUID,request:Request):return await asyncio.to_thread(service(request).get,str(rid))

@router.get('/{rid}/markdown')
async def markdown(rid:UUID,request:Request):
    result=await asyncio.to_thread(service(request).markdown,str(rid))
    return Response(result,media_type='text/markdown',headers={'Content-Disposition':f'attachment; filename="note-{rid}.md"'})

@router.get('/{rid}/revisions')
async def history(rid:UUID,request:Request,limit:int=Query(20,ge=1,le=100),offset:int=Query(0,ge=0,le=1000000)):
    return await asyncio.to_thread(service(request).history,str(rid),limit,offset)

@router.get('/{rid}/revisions/{revision_id}')
async def revision(rid:UUID,revision_id:UUID,request:Request):
    return await asyncio.to_thread(service(request).revision,str(rid),str(revision_id))

@router.post('')
async def create(payload:NoteCreate,request:Request,idempotency_key:UUID=Header()):
    result,replayed=await asyncio.to_thread(service(request).create,payload,str(idempotency_key))
    return JSONResponse(result,status_code=200 if replayed else 201)

@router.put('/{rid}')
async def edit(rid:UUID,payload:NoteEdit,request:Request,idempotency_key:UUID=Header()):
    result,_=await asyncio.to_thread(service(request).change,str(rid),payload,str(idempotency_key))
    return result

@router.post('/{rid}/trash')
async def trash(rid:UUID,payload:TaskVersion,request:Request):
    return await asyncio.to_thread(service(request).trash,str(rid),payload.expected_version)

@router.post('/{rid}/restore')
async def restore(rid:UUID,payload:TaskVersion,request:Request):
    return await asyncio.to_thread(service(request).trash,str(rid),payload.expected_version,True)
