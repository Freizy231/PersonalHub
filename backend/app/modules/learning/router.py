import asyncio
from uuid import UUID
from typing import Literal
from fastapi import APIRouter,Depends,Header,Query,Request
from ...core.security import require_session
from ..knowledge.router import Version
from .service import LearningService
from .schemas import Goal,PathWrite,Node,Assess,Session,Practice,Review
router=APIRouter(prefix='/api/v1/learning',dependencies=[Depends(require_session)])
def service(r):return LearningService(r.app.state.settings.database)
Kind=Literal['learning_goal','learning_path','knowledge_node']
@router.get('/overview')
async def overview(request:Request):return await asyncio.to_thread(service(request).overview)
@router.get('/paths/{rid}/tree')
async def tree(rid:UUID,request:Request):return await asyncio.to_thread(service(request).tree,str(rid))
@router.get('/paths/{rid}/reviews')
async def reviews(rid:UUID,request:Request):return await asyncio.to_thread(service(request).reviews,str(rid))
@router.post('/paths/{rid}/reviews')
async def review(rid:UUID,payload:Review,request:Request,idempotency_key:UUID=Header()):return await asyncio.to_thread(service(request).review,str(rid),payload,str(idempotency_key))
@router.get('/nodes/{rid}/context')
async def context(rid:UUID,request:Request):return await asyncio.to_thread(service(request).context,str(rid))
@router.post('/nodes/{rid}/assessments')
async def assess(rid:UUID,payload:Assess,request:Request,idempotency_key:UUID=Header()):return await asyncio.to_thread(service(request).assess,str(rid),payload,str(idempotency_key))
@router.post('/nodes/{rid}/practice')
async def practice(rid:UUID,payload:Practice,request:Request,idempotency_key:UUID=Header()):return await asyncio.to_thread(service(request).practice,str(rid),payload,str(idempotency_key))
@router.post('/sessions')
async def session(payload:Session,request:Request,idempotency_key:UUID=Header()):return await asyncio.to_thread(service(request).session,payload,str(idempotency_key))
@router.get('/{kind}')
async def listing(kind:Kind,request:Request,trash:bool=False,limit:int=Query(50,ge=1,le=100),offset:int=Query(0,ge=0,le=1000000)):
 return await asyncio.to_thread(service(request).list,kind,trash,limit,offset)
@router.get('/{kind}/{rid}')
async def get(kind:Kind,rid:UUID,request:Request):return await asyncio.to_thread(service(request).get,kind,str(rid))
@router.post('/learning_goal')
async def goal(payload:Goal,request:Request,idempotency_key:UUID=Header()):return await asyncio.to_thread(service(request).save,'learning_goal',payload,str(idempotency_key))
@router.put('/learning_goal/{rid}')
async def goal_update(rid:UUID,payload:Goal,request:Request,idempotency_key:UUID=Header()):return await asyncio.to_thread(service(request).save,'learning_goal',payload,str(idempotency_key),str(rid))
@router.post('/learning_path')
async def path(payload:PathWrite,request:Request,idempotency_key:UUID=Header()):return await asyncio.to_thread(service(request).save,'learning_path',payload,str(idempotency_key))
@router.put('/learning_path/{rid}')
async def path_update(rid:UUID,payload:PathWrite,request:Request,idempotency_key:UUID=Header()):return await asyncio.to_thread(service(request).save,'learning_path',payload,str(idempotency_key),str(rid))
@router.post('/knowledge_node')
async def node(payload:Node,request:Request,idempotency_key:UUID=Header()):return await asyncio.to_thread(service(request).save,'knowledge_node',payload,str(idempotency_key))
@router.put('/knowledge_node/{rid}')
async def node_update(rid:UUID,payload:Node,request:Request,idempotency_key:UUID=Header()):return await asyncio.to_thread(service(request).save,'knowledge_node',payload,str(idempotency_key),str(rid))
@router.post('/{kind}/{rid}/{action}')
async def trash(kind:Kind,rid:UUID,action:Literal['trash','restore'],payload:Version,request:Request):return await asyncio.to_thread(service(request).trash,kind,str(rid),payload.expected_version,action=='restore')
