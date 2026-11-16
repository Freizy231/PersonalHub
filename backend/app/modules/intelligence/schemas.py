from typing import Literal
from pydantic import Field, field_validator, model_validator
from ...core.outbound import public_url
from ..life.finance.schemas import Base
class SourceWrite(Base):
    kind:Literal['rss','hn','arxiv','huggingface']
    name:str=Field(min_length=1,max_length=100)
    endpoint:str=Field(default='',max_length=2048)
    # What to pull for this source: arXiv gets a query expression, HN filters titles,
    # RSS uses the endpoint it was saved with. Empty falls back to the adapter default.
    query:str=Field(default='',max_length=200)
    enabled:bool=Field(default=False,strict=True)
    expected_version:int=Field(default=0,ge=0,strict=True)

    @field_validator('query')
    @classmethod
    def query_plain(cls,value):
        # It becomes part of an upstream query string; keep it to the characters those accept.
        if any(ord(c)<32 or ord(c)==127 for c in value):raise ValueError('control characters are not allowed')
        return value.strip()


class ScheduleWrite(Base):
    enabled:bool=Field(strict=True)
    runs_per_day:int=Field(ge=1,le=24,strict=True)
    local_hours:list[str]=Field(min_length=1,max_length=24)
    expected_version:int=Field(ge=0,strict=True)

    @model_validator(mode='after')
    def hours_valid(self):
        import re
        if len(self.local_hours)!=self.runs_per_day:raise ValueError('one start time per daily run')
        if len(set(self.local_hours))!=len(self.local_hours):raise ValueError('duplicate start time')
        for value in self.local_hours:
            if not re.fullmatch(r'([01][0-9]|2[0-3]):[0-5][0-9]',value):raise ValueError('HH:MM required')
        self.local_hours=sorted(self.local_hours)
        return self
class ItemWrite(Base):
    domain:str=Field(default='',max_length=80)
    favorite:bool=Field(default=False,strict=True)
    read_state:Literal['new','later','read']='new'
    expected_version:int=Field(ge=1,strict=True)
class SubscriptionWrite(Base):
    keyword:str=Field(min_length=1,max_length=100)
    enabled:bool=Field(default=True,strict=True)
    expected_version:int=Field(default=0,ge=0,strict=True)
