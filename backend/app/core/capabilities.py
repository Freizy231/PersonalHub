"""The contract every capability adapter implements.

This module is deliberately free of business imports: it describes *shape and checks*, not
what any particular capability means. The assembly point (`caps/registry.py`) wires real
module services to these objects, and `core/` never reaches into `modules/`.

Why a contract instead of letting the model call service methods: the model produces text.
Text can only become an action after it has been matched to a registered capability, checked
against an authorization scope, validated against a typed argument schema, and executed by
code that was written by a developer. Nothing here is discovered at runtime; the registry is
a static list built when the app starts.
"""
from dataclasses import dataclass, field
from typing import Literal

from .database import HubError

Effect = Literal['read', 'create', 'update', 'outbound']


class CapabilityError(HubError):
    """A refusal from the caller's side: unknown capability, bad arguments, scope denial.

    It extends HubError so the single application error handler turns every refusal into the
    same response shape the rest of the API uses, with a code the frontend already maps.
    """



@dataclass(frozen=True)
class CapabilitySpec:
    """Everything the executor needs to know before it runs anything.

    `effect` is what makes authorization meaningful: a scope that allows reads never lets a
    create through, and `outbound` is the only effect the external-send permission covers.
    """
    id: str
    version: int
    title: str
    purpose: str
    effect: Effect
    # Which resource kinds this may touch, or empty when the capability is not resource-bound.
    kinds: frozenset = frozenset()
    # Whether a call needs a resource version / body hash to be safe against concurrent edits.
    needs_version: bool = False
    # Whether a call needs an idempotency key. Every create/update must.
    idempotent: bool = False
    # Upper bounds the executor enforces before the adapter sees anything.
    max_items: int = 100
    timeout_seconds: int = 30
    # Cancellation is cooperative: the executor stops dispatching further steps.
    cancellable: bool = True
    # Whether a committed step can be undone through module-supported operations.
    reversible: bool = False
    undo_note: str = ''
    # Named input fields the adapter validates; used to reject unknown keys early.
    fields: tuple = ()


@dataclass
class CapabilityResult:
    """What an adapter returns.

    `resource_ids` are the real records the step created or touched, so the UI can link to
    them and a later step can reference them. `summary` is shown to the person; it is never
    the only record that something happened.
    """
    summary: str
    resource_ids: tuple = ()
    data: dict = field(default_factory=dict)


def check_arguments(spec: CapabilitySpec, arguments):
    """Reject anything the capability did not declare, and anything oversized.

    Unknown keys are refused rather than ignored: a model that invents a parameter should get
    a clear failure, not a silently different operation.
    """
    if not isinstance(arguments, dict):
        raise CapabilityError('CAPABILITY_ARGUMENTS_INVALID')
    unknown = set(arguments) - set(spec.fields)
    if unknown:
        raise CapabilityError('CAPABILITY_ARGUMENTS_UNKNOWN')
    if len(arguments) > 40:
        raise CapabilityError('CAPABILITY_ARGUMENTS_INVALID')
