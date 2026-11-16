import asyncio
from typing import Literal
from uuid import UUID
from fastapi import APIRouter,Depends,Header,Query,Request
from ....core.security import require_session
from ..finance.schemas import Version
from ....core.local_records import state
from .schemas import PlanWrite,WorkoutWrite
from .service import ExerciseService
router=APIRouter(prefix='/api/v1/exercise',dependencies=[Depends(require_session)])
def service(r):return ExerciseService(r.app.state.settings.database)
@router.get('/month')
async def month(request:Request,month:str=Query(pattern=r'^[0-9]{4}-(0[1-9]|1[0-2])$'),trash:bool=False):return await asyncio.to_thread(service(request).month,month,trash)
@router.post('/plans')
async def plan(p:PlanWrite,request:Request,idempotency_key:UUID=Header()):return await asyncio.to_thread(service(request).save,'exercise_plans',p,str(idempotency_key))
@router.put('/plans/{rid}')
async def plan_edit(rid:UUID,p:PlanWrite,request:Request,idempotency_key:UUID=Header()):return await asyncio.to_thread(service(request).save,'exercise_plans',p,str(idempotency_key),str(rid))
@router.post('/records')
async def workout(p:WorkoutWrite,request:Request,idempotency_key:UUID=Header()):return await asyncio.to_thread(service(request).save,'exercise_records',p,str(idempotency_key))
@router.put('/records/{rid}')
async def workout_edit(rid:UUID,p:WorkoutWrite,request:Request,idempotency_key:UUID=Header()):return await asyncio.to_thread(service(request).save,'exercise_records',p,str(idempotency_key),str(rid))
@router.post('/{area}/{rid}/{action}')
async def change(area:Literal['plans','records'],rid:UUID,action:Literal['trash','restore'],p:Version,request:Request,idempotency_key:UUID=Header()):
    result=await asyncio.to_thread(state,request.app.state.settings.database,'exercise_plans' if area=='plans' else 'exercise_records',str(rid),p,str(idempotency_key),action=='restore')
    return {'id':result['id'],'version':result['version']}
@router.post('/export')
async def export(request:Request):return await asyncio.to_thread(service(request).export)
