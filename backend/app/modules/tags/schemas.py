import unicodedata
from typing import Annotated
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator


def tag_name(value: str) -> str:
    value = unicodedata.normalize('NFKC', value)
    if any(unicodedata.category(c).startswith('C') for c in value):
        raise ValueError('unsupported character')
    value = ' '.join(value.split())
    if not 1 <= len(value) <= 64 or len(value.casefold()) > 64:
        raise ValueError('name length')
    return value


class TagFields(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_version: int = Field(ge=1, strict=True)


class TagAssignment(TagFields):
    expected_tag_versions: dict[UUID, Annotated[int, Field(ge=1, strict=True)]] = Field(max_length=20)
    names: list[str] = Field(max_length=20)

    @field_validator('names')
    @classmethod
    def names_valid(cls, values):
        values = [tag_name(v) for v in values]
        if len({v.casefold() for v in values}) != len(values):
            raise ValueError('duplicate tag')
        return values


class TagRename(TagFields):
    name: str = Field(min_length=1, max_length=256)
    _name = field_validator('name')(tag_name)
