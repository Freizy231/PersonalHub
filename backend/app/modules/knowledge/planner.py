"""Turning model output into candidate steps — and nothing more.

The model writes a plan in a constrained JSON shape. This module is the boundary: it parses
that shape, refuses anything that is not a registered capability with declared fields, and
produces a *draft*. Nothing is written until a person adopts the draft.

Two rules are load-bearing:

* A plan that fails to parse writes no business data. The failure is reported as such.
* A step the model invents (unknown capability, undeclared field, out-of-scope resource) is
  dropped and named in `rejected`, so the person sees what was refused instead of receiving a
  quietly shorter plan.
"""
import json
import re

from ...core.capabilities import CapabilityError, check_arguments
from .adapters import SPECS

# Kinds a plan may create, in the order a learning plan naturally needs them.
CREATABLE = ('learning.goal.create', 'learning.path.create', 'learning.node.create',
             'learning.practice.create', 'notes.create')
READONLY = ('knowledge.search', 'knowledge.read', 'tasks.related')

UUID = re.compile(r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$')

PLAN_INSTRUCTIONS = """你在为一个本地优先的个人学习工作空间起草计划。
只输出一个 JSON 对象，不要输出任何解释、Markdown 代码围栏或前后文字。格式：
{"title":"计划标题","rationale":"为什么这样安排（不超过 300 字）","steps":[{"capability_id":"能力 ID","arguments":{...}}]}
允许的能力 ID 与参数：
- learning.goal.create {"title","acceptance_criteria","description"}
- learning.path.create {"goal_id","title","description"}   goal_id 用 "$goal" 表示上一步创建的目标
- learning.node.create {"path_id","title","acceptance_criteria","description"}   path_id 用 "$path" 表示上一步创建的路线
- learning.practice.create {"node_id","title","description","planned_date"}   node_id 用 "$node0"/"$node1" 依次表示已创建的第几个知识点
- notes.create {"title","body"}
规则：每个知识点必须给出可检验的 acceptance_criteria；不要编造资料中不存在的事实；不要超过 12 步；不要安排超过四周的每日任务，只给出每天的一小步。"""


def _reject(code, detail):
    return {'code': code, 'detail': detail}


def parse_plan(text, scope_id, existing_goal=False):
    """Parse and validate a model reply into a draft plan.

    Returns `{'title','rationale','steps':[...],'rejected':[...]}`. Steps carry symbolic
    references (`$goal`, `$path`, `$node0`) that adoption later resolves to real ids, so the
    model never has to know a UUID it could get wrong.
    """
    data = _load(text)
    title = str(data.get('title') or '')[:500].strip()
    if not title:
        raise CapabilityError('AI_PLAN_INVALID')
    rationale = str(data.get('rationale') or '')[:20000]
    raw_steps = data.get('steps')
    if not isinstance(raw_steps, list) or not raw_steps:
        raise CapabilityError('AI_PLAN_INVALID')
    steps, rejected, created_nodes, created_paths = [], [], 0, 0
    for index, raw in enumerate(raw_steps[:12]):
        if not isinstance(raw, dict):
            rejected.append(_reject('CAPABILITY_ARGUMENTS_INVALID', f'第 {index + 1} 步不是对象'))
            continue
        capability_id = str(raw.get('capability_id') or '')
        spec = SPECS.get(capability_id)
        if spec is None or capability_id not in CREATABLE:
            # Reads are not steps: a plan may not spend a run on reading, and unknown ids
            # must never be treated as "something close enough".
            rejected.append(_reject('CAPABILITY_UNKNOWN', f'第 {index + 1} 步：{capability_id or "未命名"}'))
            continue
        arguments = raw.get('arguments')
        try:
            arguments = _resolve_references(arguments, scope_id, created_paths, created_nodes)
            check_arguments(spec, arguments)
            _required(spec.id, arguments)
        except CapabilityError as error:
            rejected.append(_reject(error.code, f'第 {index + 1} 步：{capability_id}'))
            continue
        if capability_id == 'learning.node.create':
            created_nodes += 1
        if capability_id == 'learning.path.create':
            created_paths += 1
        steps.append({'capability_id': capability_id, 'capability_version': spec.version,
                      'arguments': arguments})
    if not steps:
        raise CapabilityError('AI_PLAN_EMPTY')
    return {'title': title, 'rationale': rationale, 'steps': steps, 'rejected': rejected}


def _load(text):
    if not isinstance(text, str):
        raise CapabilityError('AI_PLAN_INVALID')
    body = text.strip()
    if body.startswith('```'):
        # Tolerate a fenced reply, but do not try to be clever about mixed prose.
        body = re.sub(r'^```[a-zA-Z]*\s*', '', body)
        body = re.sub(r'\s*```$', '', body)
    start, end = body.find('{'), body.rfind('}')
    if start < 0 or end <= start:
        raise CapabilityError('AI_PLAN_INVALID')
    try:
        data = json.loads(body[start:end + 1])
    except json.JSONDecodeError:
        raise CapabilityError('AI_PLAN_INVALID') from None
    if not isinstance(data, dict):
        raise CapabilityError('AI_PLAN_INVALID')
    return data


def _resolve_references(arguments, scope_id, created_paths, created_nodes):
    """Replace the symbolic ids the model was told to use with references adoption resolves.

    The model never sees a real UUID, so it cannot invent one that happens to belong to
    someone else's record. A reference to something that has not been created yet in this
    same plan is a refusal.
    """
    if not isinstance(arguments, dict):
        raise CapabilityError('CAPABILITY_ARGUMENTS_INVALID')
    resolved = dict(arguments)
    for field in ('goal_id', 'path_id', 'node_id'):
        value = resolved.get(field)
        if not isinstance(value, str):
            continue
        if value == '$goal':
            if not scope_id:
                raise CapabilityError('CAPABILITY_SCOPE_DENIED')
            resolved[field] = scope_id
        elif value == '$path':
            if created_paths < 1:
                raise CapabilityError('AI_PLAN_REFERENCE_UNRESOLVED')
            resolved[field] = {'$created_path': 0}
        elif value.startswith('$node'):
            try:
                index = int(value[5:])
            except ValueError:
                raise CapabilityError('AI_PLAN_REFERENCE_UNRESOLVED') from None
            if index < 0 or index >= created_nodes:
                raise CapabilityError('AI_PLAN_REFERENCE_UNRESOLVED')
            # Referenced nodes are filled in by the adopting code, which knows the real ids.
            resolved[field] = {'$created_node': index}
    return resolved


def _required(capability_id, arguments):
    needed = {
        'learning.goal.create': ('title', 'acceptance_criteria'),
        'learning.path.create': ('goal_id', 'title'),
        'learning.node.create': ('path_id', 'title', 'acceptance_criteria'),
        'learning.practice.create': ('node_id', 'title'),
        'notes.create': ('title',),
    }.get(capability_id, ())
    for field in needed:
        value = arguments.get(field)
        if isinstance(value, str) and value.strip():
            continue
        # A reference to something created earlier in this same plan is valid even though
        # its real id does not exist yet; adoption resolves it.
        if isinstance(value, dict) and ('$created_node' in value or '$created_path' in value):
            continue
        raise CapabilityError('CAPABILITY_ARGUMENTS_INVALID')
