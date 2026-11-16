from uuid import UUID
from pydantic import Field,field_validator
from ..life.finance.schemas import Base
class ConfigWrite(Base):
    endpoint:str=Field(min_length=1,max_length=2048)
    model:str=Field(min_length=1,max_length=120)
    enabled:bool=Field(default=False,strict=True)
    token:str|None=Field(default=None,min_length=1,max_length=4096,repr=False)
    clear_token:bool=Field(default=False,strict=True)
    expected_version:int=Field(ge=0,strict=True)
    @field_validator('token')
    @classmethod
    def token_header_safe(cls,v):
        if v is not None and (not v.isascii() or any(ord(c)<33 or ord(c)>126 for c in v)):raise ValueError('invalid token')
        return v
class PreviewWrite(Base):
    question:str=Field(min_length=1,max_length=4000)
    resource_ids:list[UUID]=Field(default_factory=list,max_length=8)
    intelligence_ids:list[UUID]=Field(default_factory=list,max_length=8)
class ConfirmWrite(Base):
    preview_id:UUID
    digest:str=Field(pattern=r'^[0-9a-f]{64}$')
