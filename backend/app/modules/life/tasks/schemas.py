from datetime import date, datetime, timezone
import re
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Status = Literal['todo', 'doing', 'done', 'cancelled']


class TaskFields(BaseModel):
    model_config = ConfigDict(extra='forbid')

    @field_validator('title', check_fields=False)
    @classmethod
    def title_valid(cls, value):
        if value is None or not value.strip():
            raise ValueError('title required')
        return value.strip()

    @field_validator('planned_date', 'due_date', check_fields=False, mode='before')
    @classmethod
    def calendar_date(cls, value):
        if value is not None and (not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value)):
            raise ValueError('ISO calendar date required')
        return value

    @field_validator('timezone', check_fields=False)
    @classmethod
    def timezone_valid(cls, value):
        if value is None:
            raise ValueError('timezone required')
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError, OSError):   # e.g. "America" is a directory, not a zone
            raise ValueError('IANA timezone required') from None
        return value

    @field_validator('due_at', check_fields=False, mode='before')
    @classmethod
    def instant_string(cls, value):
        if value is not None and not isinstance(value, str):
            raise ValueError('ISO datetime required')
        return value

    @field_validator('due_at', check_fields=False)
    @classmethod
    def instant_valid(cls, value):
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError('timezone offset required')
        try:
            return value.astimezone(timezone.utc) if value else None
        except OverflowError:   # 9999-12-31T23:59:59-05:00 does not fit in UTC
            raise ValueError('datetime out of range') from None

    def values_for_storage(self):
        values = self.model_dump(mode='json', exclude_unset=True)
        if 'due_at' in values and self.due_at is not None:
            values['due_at'] = self.due_at.isoformat(timespec='microseconds').replace('+00:00', 'Z')
        return values


class TaskCreate(TaskFields):
    title: str = Field(min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=20000)
    priority: int = Field(default=2, ge=0, le=3, strict=True)
    planned_date: date | None = None
    due_date: date | None = None
    due_at: datetime | None = None
    timezone: str = Field(default='Asia/Shanghai', max_length=100)

    @model_validator(mode='after')
    def exclusive_deadline(self):
        if self.due_date and self.due_at:
            raise ValueError('only one deadline')
        return self


class TaskPatch(TaskFields):
    expected_version: int = Field(ge=1, strict=True)
    title: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=20000)
    priority: int | None = Field(default=None, ge=0, le=3, strict=True)
    planned_date: date | None = None
    due_date: date | None = None
    due_at: datetime | None = None
    timezone: str | None = Field(default=None, max_length=100)
    status: Status | None = None

    @model_validator(mode='after')
    def non_null_fields(self):
        for key in ('title', 'priority', 'timezone', 'status'):
            if key in self.model_fields_set and getattr(self, key) is None:
                raise ValueError('field cannot be null')
        if self.model_fields_set == {'expected_version'}:
            raise ValueError('empty change')
        return self


class TaskVersion(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_version: int = Field(ge=1, strict=True)
