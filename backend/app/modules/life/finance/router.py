import asyncio
from typing import Literal
from uuid import UUID
from fastapi import APIRouter,Depends,Header,Query,Request
from ....core.security import require_session
from .schemas import CategoryWrite,EntryWrite,BudgetWrite,Version
from .service import FinanceService,state
router=APIRouter(prefix='/api/v1/finance',dependencies=[Depends(require_session)])
def service(r):return FinanceService(r.app.state.settings.database)
@router.get('/categories')
async def categories(request:Request):return await asyncio.to_thread(service(request).categories)
@router.post('/categories')
async def category(p:CategoryWrite,request:Request,idempotency_key:UUID=Header()):return await asyncio.to_thread(service(request).category_save,p,str(idempotency_key))
@router.put('/categories/{rid}')
async def category_edit(rid:UUID,p:CategoryWrite,request:Request,idempotency_key:UUID=Header()):return await asyncio.to_thread(service(request).category_save,p,str(idempotency_key),str(rid))
@router.get('/entries')
async def entries(request:Request,month:str=Query(pattern=r'^[0-9]{4}-(0[1-9]|1[0-2])$'),trash:bool=False,limit:int=Query(50,ge=1,le=100),offset:int=Query(0,ge=0,le=1000000)):
    return await asyncio.to_thread(service(request).listing,month,trash,limit,offset)
@router.post('/entries')
async def entry(p:EntryWrite,request:Request,idempotency_key:UUID=Header()):return await asyncio.to_thread(service(request).save,'finance_entries',p,str(idempotency_key))
@router.put('/entries/{rid}')
async def entry_edit(rid:UUID,p:EntryWrite,request:Request,idempotency_key:UUID=Header()):return await asyncio.to_thread(service(request).save,'finance_entries',p,str(idempotency_key),str(rid))
@router.post('/entries/{rid}/{action}')
async def entry_state(rid:UUID,action:Literal['trash','restore'],p:Version,request:Request,idempotency_key:UUID=Header()):
    result=await asyncio.to_thread(state,request.app.state.settings.database,'finance_entries',str(rid),p,str(idempotency_key),action=='restore')
    return service(request).present(result)
@router.post('/budgets')
async def budget(p:BudgetWrite,request:Request,idempotency_key:UUID=Header()):return await asyncio.to_thread(service(request).save,'finance_budgets',p,str(idempotency_key))
@router.put('/budgets/{rid}')
async def budget_edit(rid:UUID,p:BudgetWrite,request:Request,idempotency_key:UUID=Header()):return await asyncio.to_thread(service(request).save,'finance_budgets',p,str(idempotency_key),str(rid))
@router.get('/report')
async def report(request:Request,month:str=Query(pattern=r'^[0-9]{4}-(0[1-9]|1[0-2])$')):return await asyncio.to_thread(service(request).report,month)
@router.post('/export')
async def export(request:Request):return await asyncio.to_thread(service(request).export)
from .schemas import ImportWrite
@router.post('/import')
async def import_data(p:ImportWrite,request:Request,idempotency_key:UUID=Header()):return await asyncio.to_thread(service(request).import_data,p,str(idempotency_key))
