from datetime import date,datetime,timezone
from uuid import UUID
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field,model_validator
class Base(BaseModel):model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
class Write(Base):
 title:str=Field(min_length=1,max_length=500)
 expected_version:int=Field(default=0,ge=0,strict=True)
 description:str=Field(default='',max_length=10000)
class Goal(Write):
 target_date:date|None=None
 status:Literal['active','completed','paused']='active'
 acceptance_criteria:str=Field(default='',max_length=10000)
class PathWrite(Write):
 goal_id:UUID|None=None
 status:Literal['active','completed','paused']='active'
class Node(Write):
 path_id:UUID
 parent_id:UUID|None=None
 position:int=Field(default=0,ge=0,le=1000000,strict=True)
 acceptance_criteria:str=Field(default='',max_length=10000)
class Assess(Base):
 expected_version:int=Field(ge=1,strict=True)
 mastery:Literal['unlearned','exposed','practiced','verified']
 conclusion:str=Field(min_length=1,max_length=10000)
 evidence:list[UUID]=Field(default_factory=list,max_length=50)
 @model_validator(mode='after')
 def unique(self):
  if len(set(self.evidence))!=len(self.evidence):raise ValueError('duplicate evidence')
  return self
class Session(Base):
 node_id:UUID
 started_at:datetime
 ended_at:datetime
 reflection_note_id:UUID|None=None
 @model_validator(mode='after')
 def interval(self):
  if self.started_at.tzinfo is None or self.ended_at.tzinfo is None:raise ValueError('timezone required')
  if not 1<=(self.ended_at-self.started_at).total_seconds()<=86400 or self.ended_at>datetime.now(timezone.utc):raise ValueError('invalid interval')
  self.started_at=self.started_at.astimezone(timezone.utc);self.ended_at=self.ended_at.astimezone(timezone.utc)
  return self
class Practice(Base):
 expected_version:int=Field(ge=1,strict=True)
 title:str=Field(min_length=1,max_length=500)
 description:str=Field(default='',max_length=10000)
 planned_date:date|None=None
class Review(Base):
 expected_version:int=Field(ge=1,strict=True)
 period_start:date
 period_end:date
 title:str=Field(min_length=1,max_length=500)
 body:str=Field(min_length=1,max_length=100000)
 @model_validator(mode='after')
 def interval(self):
  if self.period_end<self.period_start:raise ValueError('invalid period')
  return self
