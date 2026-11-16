"""Bounded, ordinary dashboard preferences. No business or private payloads."""
import ipaddress
import re
from typing import Literal
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

BASE_WIDGET_IDS = ('capture', 'tasks', 'events', 'reminders', 'shortcuts')
WIDGET_IDS = (*BASE_WIDGET_IDS, 'reading', 'learning', 'weekly', 'projects', 'meals', 'intelligence')
# Routes the shell currently serves. Kept in sync with frontend/src/modules/dashboard/types.ts.
CURRENT_TARGETS = ('/', '/tasks', '/events', '/notes', '/vault', '/diet', '/finance', '/exercise',
                   '/learning', '/library', '/projects', '/intelligence', '/ai', '/search', '/settings')
# Pre-0.14 routes stay valid so a saved layout is never rejected after an upgrade; the shell
# redirects them to the matching page.
LEGACY_TARGETS = ('/life', '/life?view=tasks', '/life?view=events', '/life?view=notes', '/life?view=vault',
                  '/life?view=diet', '/life?view=finance', '/life?view=exercise')
INTERNAL_TARGETS = frozenset((*CURRENT_TARGETS, *LEGACY_TARGETS))


def web_target(value):
    if not value or not re.fullmatch(r'[\x21-\x7e]+', value) or '\\' in value:
        raise ValueError('unsafe URL')
    if re.search(r'%(?:0[0-9a-f]|1[0-9a-f]|7f|5c)', value, re.I): raise ValueError('encoded control')
    parsed = urlsplit(value)
    scheme = parsed.scheme.lower()
    if not re.match(r'^https?://', value, re.I) or scheme not in ('http', 'https'):
        raise ValueError('http(s) only')
    if parsed.username is not None or parsed.password is not None or '?' in value or '#' in value:
        raise ValueError('no credentials or query')
    host = parsed.hostname
    if not host: raise ValueError('host required')
    port = parsed.port
    if port is not None and not 1 <= port <= 65535: raise ValueError('invalid port')
    if '%' in host: raise ValueError('no scoped host')
    if re.fullmatch(r'[0-9.]+', host): host = str(ipaddress.IPv4Address(host))
    if ':' in host:
        host = '[' + str(ipaddress.IPv6Address(host)) + ']'
    else:
        if len(host) > 253 or not re.fullmatch(r'[a-zA-Z0-9.-]+', host): raise ValueError('invalid host')
        labels = host.rstrip('.').split('.')
        if any(not re.fullmatch(r'[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?', label) for label in labels):
            raise ValueError('invalid host')
        host = host.lower()
    authority = host + (f':{port}' if port is not None else '')
    return urlunsplit((scheme, authority, parsed.path or '/', '', ''))


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Widget(StrictModel):
    id: Literal['capture', 'tasks', 'events', 'reminders', 'shortcuts', 'reading', 'learning', 'weekly', 'projects', 'meals', 'intelligence']
    enabled: bool = Field(strict=True)


class Shortcut(StrictModel):
    id: UUID
    label: str = Field(min_length=1, max_length=80)
    kind: Literal['internal', 'web']
    target: str = Field(min_length=1, max_length=2048)

    @field_validator('label')
    @classmethod
    def label_valid(cls, value):
        value.encode('utf-8')
        if not value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value): raise ValueError('invalid label')
        return value.strip()

    @model_validator(mode='after')
    def destination(self):
        if self.kind == 'internal':
            if self.target not in INTERNAL_TARGETS: raise ValueError('unknown internal route')
        else: self.target = web_target(self.target)
        return self


class Preferences(StrictModel):
    schema_version: Literal[1] = 1
    widgets: list[Widget] = Field(min_length=5, max_length=11)
    shortcuts: list[Shortcut] = Field(max_length=12)

    @field_validator('schema_version', mode='before')
    @classmethod
    def strict_schema(cls, value):
        if type(value) is not int: raise ValueError('integer schema required')
        return value

    @model_validator(mode='after')
    def unique_ids(self):
        if not set(BASE_WIDGET_IDS).issubset({w.id for w in self.widgets}) or len({w.id for w in self.widgets})!=len(self.widgets): raise ValueError('one of each widget required')
        if len({s.id for s in self.shortcuts}) != len(self.shortcuts): raise ValueError('duplicate shortcut')
        return self


class PreferencesWrite(Preferences):
    expected_version: int = Field(ge=0, strict=True)


def default_preferences():
    return {'schema_version': 1, 'widgets': [{'id': name, 'enabled': name in BASE_WIDGET_IDS} for name in WIDGET_IDS], 'shortcuts': [
        {'id': '00000000-0000-4000-8000-000000000001', 'label': '今日任务', 'kind': 'internal', 'target': '/tasks'},
        {'id': '00000000-0000-4000-8000-000000000002', 'label': '笔记', 'kind': 'internal', 'target': '/notes'},
        {'id': '00000000-0000-4000-8000-000000000003', 'label': '技术情报', 'kind': 'internal', 'target': '/intelligence'},
    ]}
