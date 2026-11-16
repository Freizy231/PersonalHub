from datetime import date, datetime, timezone
from typing import Literal
from uuid import UUID
from pydantic import Field, field_validator
from ..tasks.schemas import TaskFields
from ....core.database import HubError


def canonical(value):
    if value is None:return None
    try:
        return value.astimezone(timezone.utc).isoformat(timespec='microseconds').replace('+00:00','Z')
    except OverflowError:
        raise ValueError('datetime out of range') from None


def validate_interval(data):
    if data['is_all_day']:
        valid = data['start_date'] and data['end_date'] and data['end_date']>data['start_date'] and not data['start_at'] and not data['end_at']
    else:
        valid = data['start_at'] and data['end_at'] and data['end_at']>data['start_at'] and not data['start_date'] and not data['end_date']
    if not valid:raise HubError('EVENT_INTERVAL_INVALID',422)


class EventFields(TaskFields):
    title: str = Field(min_length=1,max_length=500)
    description: str | None = Field(default=None,max_length=20000)
    is_all_day: bool = Field(strict=True)
    start_at: datetime | None = None
    end_at: datetime | None = None
    start_date: date | None = None
    end_date: date | None = None
    timezone: str = Field(default='Asia/Shanghai',max_length=100)
    location: str | None = Field(default=None,max_length=1000)
    status: Literal['scheduled','completed','cancelled'] = 'scheduled'
    task_id: UUID | None = None

    _dates = field_validator('start_date','end_date',mode='before')(TaskFields.calendar_date.__func__)
    _instant_type = field_validator('start_at','end_at',mode='before')(TaskFields.instant_string.__func__)
    _instant = field_validator('start_at','end_at')(TaskFields.instant_valid.__func__)

    def storage(self):
        result=self.model_dump(mode='json')
        result.update(start_at=canonical(self.start_at),end_at=canonical(self.end_at),is_all_day=int(self.is_all_day))
        validate_interval(result)
        return result


class EventCreate(EventFields):
    pass


class EventEdit(EventFields):
    # Full replacement of editable facts; expected_version prevents stale form writes.
    expected_version: int = Field(ge=1,strict=True)
