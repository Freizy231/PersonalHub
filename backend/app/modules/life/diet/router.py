import asyncio
from datetime import date
from typing import Literal
from uuid import UUID
from fastapi import APIRouter, Depends, Header, Query, Request
from ....core.security import require_session
from .service import DietService
from .schemas import (IngredientWrite, RecipeWrite, MealWrite, Version, ShoppingWrite,
                      ShoppingState, ConfirmSuggestions, StockWrite)

router=APIRouter(prefix='/api/v1/diet',dependencies=[Depends(require_session)])

def service(r):
    return DietService(r.app.state.settings.database)

@router.get('/ingredients')
async def ingredients(request:Request,q:str=Query('',max_length=80),limit:int=Query(50,ge=1,le=100),offset:int=Query(0,ge=0,le=10000)):
    return await asyncio.to_thread(service(request).ingredients,q,limit,offset)

@router.post('/ingredients')
async def ingredient_create(p:IngredientWrite,request:Request,idempotency_key:UUID=Header()):
    return await asyncio.to_thread(service(request).ingredient_save,p,str(idempotency_key))

@router.put('/ingredients/{iid}')
async def ingredient_update(iid:UUID,p:IngredientWrite,request:Request,idempotency_key:UUID=Header()):
    return await asyncio.to_thread(service(request).ingredient_save,p,str(idempotency_key),str(iid))

@router.get('/recipes')
async def recipes(request:Request,q:str=Query('',max_length=200),favorite:bool=False,trash:bool=False,limit:int=Query(30,ge=1,le=100),offset:int=Query(0,ge=0,le=1000000)):
    return await asyncio.to_thread(service(request).recipes,q,favorite,trash,limit,offset)

@router.get('/recipes/{rid}')
async def recipe(rid:UUID,request:Request):
    return await asyncio.to_thread(service(request).recipe,str(rid))

@router.post('/recipes')
async def recipe_create(p:RecipeWrite,request:Request,idempotency_key:UUID=Header()):
    return await asyncio.to_thread(service(request).recipe_save,p,str(idempotency_key))

@router.put('/recipes/{rid}')
async def recipe_update(rid:UUID,p:RecipeWrite,request:Request,idempotency_key:UUID=Header()):
    return await asyncio.to_thread(service(request).recipe_save,p,str(idempotency_key),str(rid))

@router.post('/recipes/{rid}/{action}')
async def recipe_state(rid:UUID,action:Literal['trash','restore'],p:Version,request:Request,idempotency_key:UUID=Header()):
    return await asyncio.to_thread(service(request).recipe_trash,str(rid),p,str(idempotency_key),action=='restore')

@router.get('/meals')
async def meals(week_start:date,request:Request):
    return await asyncio.to_thread(service(request).meals,week_start)

@router.get('/today')
async def today(local_date:date,request:Request):
    return await asyncio.to_thread(service(request).today,local_date)

@router.post('/meals')
async def meal_create(p:MealWrite,request:Request,idempotency_key:UUID=Header()):
    return await asyncio.to_thread(service(request).meal_save,p,str(idempotency_key))

@router.put('/meals/{mid}')
async def meal_update(mid:UUID,p:MealWrite,request:Request,idempotency_key:UUID=Header()):
    return await asyncio.to_thread(service(request).meal_save,p,str(idempotency_key),str(mid))

@router.post('/meals/{mid}/remove')
async def meal_remove(mid:UUID,p:Version,request:Request,idempotency_key:UUID=Header()):
    return await asyncio.to_thread(service(request).meal_remove,str(mid),p,str(idempotency_key))

@router.get('/shopping')
async def shopping(week_start:date,request:Request):
    return await asyncio.to_thread(service(request).shopping,week_start)

@router.post('/shopping/{week_start}')
async def shopping_create(week_start:date,p:ShoppingWrite,request:Request,idempotency_key:UUID=Header()):
    return await asyncio.to_thread(service(request).shopping_save,week_start,p,str(idempotency_key))

@router.put('/shopping/{week_start}/{sid}')
async def shopping_update(week_start:date,sid:UUID,p:ShoppingWrite,request:Request,idempotency_key:UUID=Header()):
    return await asyncio.to_thread(service(request).shopping_save,week_start,p,str(idempotency_key),str(sid))

@router.post('/shopping/{week_start}/{sid}/state')
async def shopping_state(week_start:date,sid:UUID,p:ShoppingState,request:Request,idempotency_key:UUID=Header()):
    return await asyncio.to_thread(service(request).shopping_state,week_start,str(sid),p,str(idempotency_key))

@router.get('/suggestions')
async def suggestions(week_start:date,request:Request):
    return await asyncio.to_thread(service(request).suggestions,week_start)

@router.post('/suggestions/{week_start}/confirm')
async def confirm(week_start:date,p:ConfirmSuggestions,request:Request,idempotency_key:UUID=Header()):
    return await asyncio.to_thread(service(request).confirm,week_start,p,str(idempotency_key))

@router.get('/stock')
async def stock(request:Request,q:str=Query('',max_length=80),limit:int=Query(50,ge=1,le=100),offset:int=Query(0,ge=0,le=10000)):
    return await asyncio.to_thread(service(request).stock,q,limit,offset)

@router.put('/stock')
async def stock_save(p:StockWrite,request:Request,idempotency_key:UUID=Header()):
    return await asyncio.to_thread(service(request).stock_save,p,str(idempotency_key))

@router.post('/stock/{sid}/remove')
async def stock_remove(sid:UUID,p:Version,request:Request,idempotency_key:UUID=Header()):
    return await asyncio.to_thread(service(request).stock_remove,str(sid),p,str(idempotency_key))
