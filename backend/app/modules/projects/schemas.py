from datetime import date
from pathlib import PurePosixPath
from typing import Literal
from uuid import UUID
import unicodedata
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from ..library.catalog import safe_url
from ..life.tasks.schemas import TaskCreate


class Base(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class Version(Base):
    expected_version: int = Field(ge=1, strict=True)


def validate_local_directory(value):
    if not value:
        return value
    if (not value.startswith('/') or value == '/' or '\\' in value
            or any(unicodedata.category(c).startswith('C') for c in value)
            or any(p in ('', '.', '..') for p in value[1:].split('/'))
            or len(PurePosixPath(value).parts)>128):
        raise ValueError('absolute directory without traversal required')
    return value


class ProjectWrite(Base):
    title: str = Field(min_length=1, max_length=500)
    description: str = Field(default='', max_length=10000)
    objective: str = Field(default='', max_length=10000)
    status: Literal['planned','active','paused','completed','archived'] = 'planned'
    local_directory: str = Field(default='', max_length=2048)
    repository_url: str = Field(default='', max_length=2048)
    start_date: date | None = None
    target_date: date | None = None
    expected_version: int = Field(default=0, ge=0, strict=True)

    _directory = field_validator('local_directory')(validate_local_directory)

    @field_validator('repository_url')
    @classmethod
    def repository(cls, v):
        return safe_url(v, True)

    @model_validator(mode='after')
    def dates(self):
        if self.start_date and self.target_date and self.target_date<self.start_date:
            raise ValueError('target before start')
        return self


class MilestoneWrite(Version):
    title: str = Field(min_length=1, max_length=500)
    description: str = Field(default='', max_length=10000)
    target_date: date | None = None
    status: Literal['todo','doing','done'] = 'todo'
    position: int = Field(default=0, ge=0, le=1000000, strict=True)
    expected_milestone_version: int = Field(default=0, ge=0, strict=True)


class MilestoneVersion(Version):
    expected_milestone_version: int = Field(ge=1, strict=True)


class TaskLink(Version):
    task_id: UUID
    milestone_id: UUID | None = None


class ProjectTaskCreate(TaskCreate):
    expected_version: int = Field(ge=1, strict=True)
    milestone_id: UUID | None = None


class EntryLink(Version):
    note_id: UUID
    entry_kind: Literal['log','experiment','summary'] = 'log'


class EntryCreate(Version):
    title: str = Field(min_length=1, max_length=500)
    body: str = Field(min_length=1, max_length=100000)
    entry_kind: Literal['log','experiment','summary'] = 'log'
