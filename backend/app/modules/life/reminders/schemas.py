from datetime import datetime
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator
from ..tasks.schemas import TaskFields

class ReminderCreate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    resource_id: UUID
    expected_resource_version: int = Field(ge=1,strict=True)
    remind_at: datetime
    _instant_type = field_validator('remind_at',mode='before')(TaskFields.instant_string.__func__)
    _instant = field_validator('remind_at')(TaskFields.instant_valid.__func__)
