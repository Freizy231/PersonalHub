"""Request bodies for the AI workspace. Every field is bounded and extra keys are refused."""
from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class Base(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)


class ConversationStart(Base):
    scope_id: UUID | None = None
    title: str = Field(default='', max_length=500)


class MessageWrite(Base):
    body: str = Field(min_length=1, max_length=20000)
    source_ids: list[UUID] = Field(default_factory=list, max_length=8)


class PlanDraft(Base):
    conversation_id: UUID | None = None
    scope_id: UUID | None = None
    question: str = Field(min_length=1, max_length=4000)
    # The person's own words; the model turns this into candidate steps.
    intent: str = Field(default='', max_length=4000)


class AdoptWrite(Base):
    pass


class GrantWrite(Base):
    kind: Literal['local_write', 'outbound']
    scope_id: UUID | None = None
    enabled: bool = Field(strict=True)
    days: int | None = Field(default=None, ge=1, le=365)
    endpoint: str = Field(default='', max_length=2048)
    model: str = Field(default='', max_length=120)


class PreferencesWrite(Base):
    daily_minutes: int = Field(ge=5, le=600, strict=True)
    session_minutes: int = Field(ge=5, le=240, strict=True)
    review_frequency: Literal['off', 'daily', 'weekly']
    reminder_hour: int = Field(ge=0, le=23, strict=True)
    template_id: UUID | None = None
    expected_version: int = Field(ge=0, strict=True)


class TemplateStep(Base):
    capability_id: str = Field(min_length=1, max_length=80)
    arguments: dict = Field(default_factory=dict)


class TemplateWrite(Base):
    name: str = Field(min_length=1, max_length=200)
    purpose: str = Field(default='', max_length=1000)
    enabled: bool = Field(default=True, strict=True)
    steps: list[TemplateStep] = Field(min_length=1, max_length=12)


class TemplateToggle(Base):
    enabled: bool = Field(strict=True)


class RunTemplate(Base):
    scope_id: UUID
    template_id: UUID


class ReviewRequest(Base):
    scope_id: UUID
    run_id: UUID | None = None


class ScheduleRequest(Base):
    scope_id: UUID
    days: int = Field(default=14, ge=1, le=60, strict=True)
