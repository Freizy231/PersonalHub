from datetime import date
from typing import Literal
from uuid import UUID
from pydantic import Field
from ..finance.schemas import Base
Kind=Literal['run','walk','strength','cycle','other']
class PlanWrite(Base):
    title:str=Field(min_length=1,max_length=200)
    local_date:date
    kind:Kind='run'
    duration_seconds:int=Field(default=0,ge=0,le=604800,strict=True)
    distance_meters:int=Field(default=0,ge=0,le=1000000,strict=True)
    task_id:UUID|None=None
    event_id:UUID|None=None
    cancelled:bool=Field(default=False,strict=True)
    expected_version:int=Field(default=0,ge=0,strict=True)
class WorkoutWrite(Base):
    local_date:date
    kind:Kind='run'
    duration_seconds:int=Field(ge=1,le=604800,strict=True)
    distance_meters:int=Field(default=0,ge=0,le=1000000,strict=True)
    memo:str=Field(default='',max_length=2000)
    plan_id:UUID|None=None
    expected_version:int=Field(default=0,ge=0,strict=True)
