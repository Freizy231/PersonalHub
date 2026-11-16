from datetime import date
from typing import Literal
from pydantic import Field,field_validator
from ...life.tasks.schemas import TaskFields

class NoteCreate(TaskFields):
    title: str=Field(min_length=1,max_length=500)
    body: str=Field(default='',max_length=200000)
    note_kind: Literal['quick','journal','knowledge','experiment']='quick'
    local_date: date|None=None
    _date=field_validator('local_date',mode='before')(TaskFields.calendar_date.__func__)

    @field_validator('body','title')
    @classmethod
    def utf8_body(cls,value):
        try:value.encode('utf-8')
        except UnicodeEncodeError:raise ValueError('valid UTF-8 required') from None
        return value

class NoteEdit(NoteCreate):
    expected_version: int=Field(ge=1,strict=True)
    expected_hash: str=Field(pattern=r'^[a-f0-9]{64}$')
