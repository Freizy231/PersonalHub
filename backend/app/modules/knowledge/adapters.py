"""Adapters that connect real module services to the capability contract.

Each adapter is a thin, typed wrapper: it validates the arguments it accepts, calls the module's
public service, and returns the IDs of what it actually created or touched. It never
re-implements business rules — the version checks, tree integrity, visibility and privacy
filtering all still run inside the services below it.

The set here is the first tranche named by the requirement: search and source reading,
learning goal/path/node reads and writes, practice creation, related task reads and
scheduling, and ordinary note creation. Adding a capability means adding a spec + a function
here and registering it; the executor never changes.
"""
from datetime import date

from ...core.capabilities import CapabilityError, CapabilityResult, CapabilitySpec, check_arguments
from ...core.database import HubError, connection
from ...core.operations import request_hash
from ..knowledge.search import SearchService
from ..knowledge.service import KnowledgeService, rows, visible
from ..learning.schemas import Goal, Node, PathWrite, Practice
from ..learning.service import LearningService
from ..library.notes.schemas import NoteCreate
from ..library.notes.service import NoteService
from ..life.tasks.schemas import TaskCreate, TaskPatch
from ..life.tasks.service import TaskService

# Resource kinds a learning goal may legitimately own or reference. Anything else — finance,
# exercise, the vault, unrelated life tasks — is refused by scope, not by accident.
LEARNING_KINDS = frozenset({'learning_goal', 'learning_path', 'knowledge_node', 'note', 'document',
                            'book', 'bookmark', 'course'})


def _spec(**kw):
    return CapabilitySpec(**kw)


SPECS = {
    'knowledge.search': _spec(
        id='knowledge.search', version=1, title='检索资料',
        purpose='在授权资料范围内按关键词检索，返回标题与片段。',
        effect='read', kinds=LEARNING_KINDS, max_items=20,
        fields=('q', 'kind', 'limit')),
    'knowledge.read': _spec(
        id='knowledge.read', version=1, title='读取资料',
        purpose='读取一份资料的标题与正文摘要，用于形成计划或复盘依据。',
        effect='read', kinds=LEARNING_KINDS, max_items=8,
        fields=('ids',)),
    'learning.goal.create': _spec(
        id='learning.goal.create', version=1, title='创建学习目标',
        purpose='创建一个学习目标，含完成标准。',
        effect='create', kinds=frozenset({'learning_goal'}), idempotent=True, reversible=True,
        undo_note='可移入回收站恢复原状。',
        fields=('title', 'acceptance_criteria', 'description')),
    'learning.path.create': _spec(
        id='learning.path.create', version=1, title='创建学习路线',
        purpose='在某个学习目标下创建一条路线。',
        effect='create', kinds=frozenset({'learning_path'}), idempotent=True, reversible=True,
        undo_note='可移入回收站恢复原状。',
        fields=('goal_id', 'title', 'description')),
    'learning.node.create': _spec(
        id='learning.node.create', version=1, title='创建知识点',
        purpose='在路线下创建知识点，必须写明验收标准。',
        effect='create', kinds=frozenset({'knowledge_node'}), idempotent=True, reversible=True,
        undo_note='可移入回收站恢复原状。',
        fields=('path_id', 'title', 'acceptance_criteria', 'description')),
    'learning.practice.create': _spec(
        id='learning.practice.create', version=1, title='创建练习任务',
        purpose='为知识点创建一个真实任务作为练习，并建立关联。',
        effect='create', kinds=frozenset({'task', 'knowledge_node'}), idempotent=True, reversible=True,
        undo_note='任务可删除，关联随之移除。',
        fields=('node_id', 'title', 'description')),
    'learning.schedule': _spec(
        id='learning.schedule', version=1, title='安排今日练习',
        purpose='把一项练习任务的计划日设为某一天。',
        effect='update', kinds=frozenset({'task'}), needs_version=True, idempotent=True,
        reversible=True, undo_note='可改回原计划日。',
        fields=('task_id', 'planned_date', 'expected_version')),
    'notes.create': _spec(
        id='notes.create', version=1, title='创建笔记',
        purpose='新建一条普通笔记，可作为学习成果。',
        effect='create', kinds=frozenset({'note'}), idempotent=True, reversible=True,
        undo_note='可移入回收站恢复原状。',
        fields=('title', 'body', 'note_kind', 'local_date')),
    'tasks.related': _spec(
        id='tasks.related', version=1, title='读取相关练习',
        purpose='读取某个知识点上已关联的练习任务。',
        effect='read', kinds=frozenset({'task', 'knowledge_node'}), max_items=50,
        fields=('node_id',)),
}

REFUSED_KINDS = ("理财、运动、私密库和生活日程不在 AI 可访问范围内。"
                 "只能访问学习目标、路线、知识点、普通笔记、文献与练习任务。")


class Adapters:
    """Holds the module services and one callable per registered capability.

    Services are constructed per call against the current database path so a restored or
    upgraded data root is picked up without restarting the process.
    """

    def __init__(self, settings):
        self.settings = settings
        self.database = settings.database
        self.search = SearchService(self.database)
        self.knowledge = KnowledgeService(self.database)
        self.learning = LearningService(self.database)
        self.notes = NoteService(self.database)
        self.tasks = TaskService(self.database)
        self.calls = {
            'knowledge.search': self._search,
            'knowledge.read': self._read,
            'learning.goal.create': self._goal,
            'learning.path.create': self._path,
            'learning.node.create': self._node,
            'learning.practice.create': self._practice,
            'learning.schedule': self._schedule,
            'notes.create': self._note,
            'tasks.related': self._related,
        }

    # ------------------------------------------------------------------ scope

    def assert_scope(self, spec, scope_id):
        """Refuse an operation outside the authorized learning scope.

        The scope is a learning goal (or a path/node belonging to one). Reads and writes that
        touch a resource must sit inside it. This is checked here, before the adapter runs, so
        a malformed or hostile model response cannot reach a service with a foreign id.
        """
        if not spec.kinds:
            return
        if not spec.kinds <= LEARNING_KINDS:
            raise CapabilityError('CAPABILITY_SCOPE_DENIED', 403)
        if scope_id:
            with connection(self.database) as con:
                try:
                    visible(con, scope_id)
                except HubError:
                    raise CapabilityError('CAPABILITY_SCOPE_DENIED', 403) from None

    def _in_scope(self, con, scope_id, resource_id):
        """True when `resource_id` belongs to the authorized learning goal subtree."""
        if not scope_id:
            return False
        if resource_id == scope_id:
            return True
        if con.exec_driver_sql('SELECT 1 FROM learning_paths WHERE resource_id=? AND goal_id=?',
                               (resource_id, scope_id)).first():
            return True
        # A node is in scope when its path hangs off that goal.
        return bool(con.exec_driver_sql(
            'SELECT 1 FROM knowledge_nodes n JOIN learning_paths p ON p.resource_id=n.path_id '
            'WHERE n.resource_id=? AND p.goal_id=?', (resource_id, scope_id)).first())

    def _task_in_scope(self, con, scope_id, task_id):
        """A practice task is in scope only through a node that sits inside the goal."""
        return bool(con.exec_driver_sql(
            'SELECT 1 FROM learning_node_tasks l JOIN knowledge_nodes n ON n.resource_id=l.node_id '
            'JOIN learning_paths p ON p.resource_id=n.path_id '
            'WHERE l.task_id=? AND p.goal_id=?', (task_id, scope_id)).first())

    def _checked(self, scope_id, kind, rid):
        with connection(self.database) as con:
            try:
                visible(con, rid)
            except HubError:
                raise CapabilityError('CAPABILITY_SCOPE_DENIED', 403) from None
            r = con.exec_driver_sql('SELECT kind FROM resources WHERE id=?', (rid,)).scalar_one()
            if r != kind:
                raise CapabilityError('CAPABILITY_SCOPE_DENIED', 403)
            if not self._in_scope(con, scope_id, rid):
                raise CapabilityError('CAPABILITY_SCOPE_DENIED', 403)

    # ------------------------------------------------------------------- calls

    def _search(self, arguments, scope_id, key):
        q = str(arguments.get('q') or '')[:200]
        limit = min(int(arguments.get('limit') or 10), 20)
        result = self.search.query(q, str(arguments.get('kind') or ''), limit, 0)
        items = [{'id': i['id'], 'kind': i['kind'], 'title': i['title'], 'snippet': i.get('snippet', '')}
                 for i in result.get('items', []) if i.get('kind') in LEARNING_KINDS]
        return CapabilityResult(f'检索到 {len(items)} 条资料', tuple(i['id'] for i in items), {'items': items})

    def _read(self, arguments, scope_id, key):
        ids = arguments.get('ids') or []
        if not isinstance(ids, list) or len(ids) > 8:
            raise CapabilityError('CAPABILITY_ARGUMENTS_INVALID')
        found = []
        for rid in ids:
            with connection(self.database) as con:
                try:
                    r = visible(con, str(rid))
                except HubError:
                    raise CapabilityError('CAPABILITY_SCOPE_DENIED', 403) from None
                if r['kind'] not in LEARNING_KINDS:
                    raise CapabilityError('CAPABILITY_SCOPE_DENIED', 403)
                body, digest = self.search.source(con, r)
            found.append({'id': r['id'], 'kind': r['kind'], 'title': r['title'],
                          'version': r['version'], 'source_hash': digest,
                          'text': body[:6000], 'truncated': len(body) > 6000})
        return CapabilityResult(f'读取 {len(found)} 份资料', tuple(f['id'] for f in found), {'items': found})

    def _goal(self, arguments, scope_id, key):
        payload = Goal(title=_title(arguments), acceptance_criteria=_text(arguments, 'acceptance_criteria'),
                       description=_text(arguments, 'description'))
        result = self.learning.save('learning_goal', payload, key)
        return CapabilityResult(f'已创建学习目标「{result["title"]}」', (result['id'],), {'id': result['id']})

    def _path(self, arguments, scope_id, key):
        goal_id = _uuid(arguments, 'goal_id')
        if scope_id and goal_id != scope_id:
            raise CapabilityError('CAPABILITY_SCOPE_DENIED', 403)
        payload = PathWrite(title=_title(arguments), goal_id=goal_id, description=_text(arguments, 'description'))
        result = self.learning.save('learning_path', payload, key)
        return CapabilityResult(f'已创建路线「{result["title"]}」', (result['id'],), {'id': result['id']})

    def _node(self, arguments, scope_id, key):
        path_id = _uuid(arguments, 'path_id')
        criteria = _text(arguments, 'acceptance_criteria')
        if not criteria:
            # A node without a finish line can never be verified; require it up front.
            raise CapabilityError('CAPABILITY_ARGUMENTS_INVALID')
        self._checked(scope_id, 'learning_path', path_id)
        payload = Node(title=_title(arguments), path_id=path_id, acceptance_criteria=criteria,
                       description=_text(arguments, 'description'))
        result = self.learning.save('knowledge_node', payload, key)
        return CapabilityResult(f'已创建知识点「{result["title"]}」', (result['id'],), {'id': result['id']})

    def _practice(self, arguments, scope_id, key):
        node_id = _uuid(arguments, 'node_id')
        self._checked(scope_id, 'knowledge_node', node_id)
        payload = Practice(expected_version=_version_of(self.database, node_id),
                           title=_title(arguments), description=_text(arguments, 'description'),
                           planned_date=_day(arguments, 'planned_date'))
        result = self.learning.practice(node_id, payload, key)
        return CapabilityResult(f'已创建练习「{result["title"]}」', (result['id'],), {'id': result['id']})

    def _schedule(self, arguments, scope_id, key):
        task_id = _uuid(arguments, 'task_id')
        with connection(self.database) as con:
            try:
                visible(con, task_id)
            except HubError:
                raise CapabilityError('CAPABILITY_SCOPE_DENIED', 403) from None
            if scope_id and not self._task_in_scope(con, scope_id, task_id):
                # Only practice tasks inside the authorized goal may be rescheduled; ordinary
                # life tasks are not part of this scope even when they are visible elsewhere.
                raise CapabilityError('CAPABILITY_SCOPE_DENIED', 403)
        day = str(arguments.get('planned_date') or '')[:10]
        try:
            date.fromisoformat(day)
        except ValueError:
            raise CapabilityError('CAPABILITY_ARGUMENTS_INVALID') from None
        expected = arguments.get('expected_version')
        if not isinstance(expected, int) or expected < 1:
            raise CapabilityError('CAPABILITY_ARGUMENTS_INVALID')
        payload = TaskPatch(expected_version=expected, planned_date=date.fromisoformat(day))
        result = self.tasks.change(task_id, payload)
        return CapabilityResult(f'已把「{result["title"]}」安排到 {day}', (result['id'],),
                                {'id': result['id'], 'version': result['version']})

    def _note(self, arguments, scope_id, key):
        payload = NoteCreate(title=_title(arguments), body=_text(arguments, 'body', 200000),
                             note_kind=arguments.get('note_kind') or 'quick')
        result, _ = self.notes.create(payload, key)
        return CapabilityResult(f'已创建笔记「{result["title"]}」', (result['id'],), {'id': result['id']})

    def _related(self, arguments, scope_id, key):
        node_id = _uuid(arguments, 'node_id')
        self._checked(scope_id, 'knowledge_node', node_id)
        context = self.learning.context(node_id)
        items = [{'id': t['id'], 'title': t['title'], 'status': t['status'],
                  'planned_date': t.get('planned_date')} for t in context.get('tasks', [])]
        return CapabilityResult(f'该知识点关联 {len(items)} 项练习', tuple(i['id'] for i in items), {'items': items})


# ---------------------------------------------------------------- argument helpers

def _title(arguments):
    value = str(arguments.get('title') or '').strip()
    if not value:
        raise CapabilityError('CAPABILITY_ARGUMENTS_INVALID')
    return value[:500]


def _text(arguments, name, limit=10000):
    return str(arguments.get(name) or '')[:limit]


def _day(arguments, name):
    """An optional ISO date; an unparseable value is a refusal, not a silent today."""
    raw = str(arguments.get(name) or '').strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        raise CapabilityError('CAPABILITY_ARGUMENTS_INVALID') from None


def _uuid(arguments, name):
    value = str(arguments.get(name) or '').strip()
    if len(value) != 36 or value.count('-') != 4:
        raise CapabilityError('CAPABILITY_ARGUMENTS_INVALID')
    return value


def _version_of(database, rid):
    with connection(database) as con:
        return con.exec_driver_sql('SELECT version FROM resources WHERE id=?', (rid,)).scalar_one()
