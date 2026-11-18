"""The AI workspace: conversations, plans, adoption, grants, preferences and templates.

This service owns the records that make an AI-assisted flow reviewable — what was proposed,
what was adopted, what ran, who allowed it — while every business fact still lives in its own
module. Nothing here creates learning nodes or tasks itself: adoption builds a run and hands
it to `Executor`, which calls the registered adapters.

The most important rule in this file is that **adoption resolves references against what the
run has actually created**. A plan is written before anything exists, so its later steps point
at "$path" / "$node0"; here those become real ids taken from the executor's own step results.
If a reference cannot be resolved the step is skipped and reported, never guessed.
"""
import hashlib
import json
from datetime import date, datetime, timedelta
from uuid import uuid4

from ...core.capabilities import CapabilityError
from ...core.database import HubError, connection, utc_now
from ...core.note_files import NoteFiles
from ...core.operations import activity
from .adapters import SPECS
from .executor import Executor
from .planner import parse_plan

DEFAULT_PREFERENCES = {
    'daily_minutes': 40, 'session_minutes': 25, 'review_frequency': 'weekly',
    'reminder_hour': 9, 'template_id': None, 'last_reviewed_at': None,
}


def _today():
    return datetime.now().astimezone().date()


class WorkspaceService:
    def __init__(self, settings):
        self.settings = settings
        self.database = settings.database
        self.root = settings.data_root
        self.executor = Executor(settings)

    # ------------------------------------------------------------- conversations

    def conversation(self, conversation_id, owner, limit=40):
        with connection(self.database) as con:
            row = con.exec_driver_sql('SELECT * FROM ai_conversations WHERE id=?', (conversation_id,)).mappings().first()
            if not row:
                raise HubError('LOCAL_RECORD_NOT_FOUND', 404)
            messages = con.exec_driver_sql(
                'SELECT id,seq,role,body_path,body_sha256,source_ids_json,origin,created_at '
                'FROM ai_messages WHERE conversation_id=? ORDER BY seq DESC LIMIT ?',
                (conversation_id, limit)).mappings().all()
            stale = self._stale_sources(con, messages)
        notes = NoteFiles(self.root)
        items = []
        for message in reversed(messages):
            body = ''
            if message['body_path']:
                try:
                    body = notes.body(message['body_path'], message['body_sha256'])
                except Exception:
                    # A missing body is reported as such; it never silently becomes empty text.
                    body = ''
            items.append({'id': message['id'], 'seq': message['seq'], 'role': message['role'],
                          'body': body, 'body_missing': bool(message['body_path']) and not body,
                          'sources': json.loads(message['source_ids_json']),
                          'origin': message['origin'], 'created_at': message['created_at'],
                          'stale': any(str(s) in stale for s in json.loads(message['source_ids_json']))})
        return {**row, 'messages': items, 'stale_sources': sorted(stale),
                'stale_notice': '引用的资料已修改或不可访问，这段内容不能作为当前依据。' if stale else ''}

    @staticmethod
    def _stale_sources(con, messages):
        """Sources a conversation was built from that have since changed or gone.

        Compared against the version and hash recorded when the message was written, so an
        edit counts as stale even though the resource still exists. A deleted or hidden source
        is reported by id only — the conversation must not become a way to read the title of
        something the person has since hidden.
        """
        ids = [str(m['id']) for m in messages]
        if not ids:
            return set()
        marks = ','.join('?' for _ in ids)
        refs = con.exec_driver_sql(
            f'SELECT r.resource_id,r.resource_version,r.source_hash,x.version,x.deleted_at '
            f'FROM ai_source_refs r LEFT JOIN resources x ON x.id=r.resource_id '
            f"WHERE r.owner_table='message' AND r.owner_id IN ({marks})", tuple(ids)).all()
        stale = set()
        for rid, expected_version, expected_hash, version, deleted_at in refs:
            if version is None or deleted_at or version != expected_version:
                stale.add(rid)
        return stale

    def clear_conversation(self, conversation_id):
        """Remove a conversation, its messages and their managed files.

        The records go first; file removal happens after the commit, so a failure leaves a
        harmless orphan file rather than a row pointing at nothing.
        """
        with connection(self.database) as con:
            paths = [r[0] for r in con.exec_driver_sql(
                'SELECT body_path FROM ai_messages WHERE conversation_id=?', (conversation_id,)).fetchall()]
        with connection(self.database, write=True) as con:
            if not con.exec_driver_sql('SELECT 1 FROM ai_conversations WHERE id=?', (conversation_id,)).first():
                raise HubError('LOCAL_RECORD_NOT_FOUND', 404)
            con.exec_driver_sql("UPDATE ai_plans SET conversation_id=NULL WHERE conversation_id=?",
                                (conversation_id,))
            con.exec_driver_sql('DELETE FROM ai_messages WHERE conversation_id=?', (conversation_id,))
            con.exec_driver_sql('DELETE FROM ai_conversations WHERE id=?', (conversation_id,))
        notes = NoteFiles(self.root)
        for path in paths:
            if path:
                try:
                    notes.unlink(path)
                except HubError:
                    pass
        return {'status': 'cleared', 'conversation_id': conversation_id}

    def start_conversation(self, scope_id, title, owner):
        cid, now = str(uuid4()), utc_now()
        with connection(self.database, write=True) as con:
            con.exec_driver_sql(
                'INSERT INTO ai_conversations(id,scope_id,scope_kind,title,state,summary_text,summary_upto,'
                "created_at,updated_at) VALUES(?,?,'learning_goal',?,'active','',0,?,?)",
                (cid, scope_id, title[:500], now, now))
        return {'id': cid, 'scope_id': scope_id, 'title': title}

    def append_message(self, conversation_id, role, body, sources=(), origin='user', refs=()):
        """Store a message body as a managed file and keep only the reference in SQLite.

        Conversation bodies live under `notes/ai/...` so they reuse the ordinary note-file
        protocol: same 0600 permissions, same symlink refusal, and — importantly — they are
        picked up by the existing snapshot/inventory/restore code without any special case.
        """
        with connection(self.database) as con:
            row = con.exec_driver_sql('SELECT coalesce(max(seq),-1) FROM ai_messages WHERE conversation_id=?',
                                      (conversation_id,)).scalar_one()
        seq = int(row) + 1
        token = uuid4().hex[:8]
        relative = f'notes/ai/conversations/{conversation_id}/{seq:04d}-{token}.md'
        temporary = f'notes/ai/conversations/{conversation_id}/.{seq:04d}-{token}.tmp'
        data = body.encode('utf-8')
        NoteFiles(self.root).write_revision(relative, temporary, data)
        mid, now = str(uuid4()), utc_now()
        with connection(self.database, write=True) as con:
            con.exec_driver_sql(
                'INSERT INTO ai_messages(id,conversation_id,seq,role,body_path,body_sha256,source_ids_json,'
                'origin,created_at) VALUES(?,?,?,?,?,?,?,?,?)',
                (mid, conversation_id, seq, role, relative, hashlib.sha256(data).hexdigest(),
                 json.dumps([str(s) for s in sources]), origin, now))
            con.exec_driver_sql('UPDATE ai_conversations SET updated_at=? WHERE id=?', (now, conversation_id))
            for ref in refs:
                # `sources_for` returns version + hash; record them so a later edit is visible.
                con.exec_driver_sql(
                    'INSERT OR REPLACE INTO ai_source_refs(id,owner_table,owner_id,resource_id,'
                    'resource_version,source_hash,created_at) VALUES(?,?,?,?,?,?,?)',
                    (str(uuid4()), 'message', mid, ref['id'], ref['version'], ref['source_hash'], now))
        return {'id': mid, 'seq': seq, 'role': role, 'body': body}

    # -------------------------------------------------------------------- plans

    def draft_plan(self, text, conversation_id, scope_id, model=''):
        """Validate a model reply into a stored draft. No business data is written."""
        parsed = parse_plan(text, scope_id)
        plan_id, now = str(uuid4()), utc_now()
        with connection(self.database, write=True) as con:
            con.exec_driver_sql(
                'INSERT INTO ai_plans(id,conversation_id,scope_id,title,rationale,state,model,created_at,'
                "adopted_at) VALUES(?,?,?,?,?,'draft',?,?,NULL)",
                (plan_id, conversation_id, scope_id, parsed['title'], parsed['rationale'], model, now))
            for position, step in enumerate(parsed['steps']):
                con.exec_driver_sql(
                    'INSERT INTO ai_plan_steps(id,plan_id,position,capability_id,capability_version,'
                    "arguments_json,state,result_ref,created_at) VALUES(?,?,?,?,?,?,'pending','',?)",
                    (str(uuid4()), plan_id, position, step['capability_id'], step['capability_version'],
                     json.dumps(step['arguments'], ensure_ascii=False), now))
        return {'id': plan_id, 'title': parsed['title'], 'rationale': parsed['rationale'],
                'steps': self._plan_steps(plan_id), 'rejected': parsed['rejected'], 'state': 'draft'}

    def get_plan(self, plan_id):
        with connection(self.database) as con:
            plan = con.exec_driver_sql('SELECT * FROM ai_plans WHERE id=?', (plan_id,)).mappings().first()
            if not plan:
                raise HubError('LOCAL_RECORD_NOT_FOUND', 404)
        return {**plan, 'steps': self._plan_steps(plan_id)}

    def _plan_steps(self, plan_id):
        with connection(self.database) as con:
            rows = con.exec_driver_sql(
                'SELECT position,capability_id,arguments_json,state FROM ai_plan_steps WHERE plan_id=? '
                'ORDER BY position', (plan_id,)).mappings().all()
        return [{'position': r['position'], 'capability_id': r['capability_id'],
                 'arguments': json.loads(r['arguments_json']), 'state': r['state']} for r in rows]

    def adopt(self, plan_id, owner):
        """Create a run from the plan and execute it, resolving symbolic references as it goes.

        Re-adopting the same plan returns the existing run instead of creating a second set of
        goals and tasks.
        """
        plan = self.get_plan(plan_id)
        with connection(self.database) as con:
            existing = con.exec_driver_sql(
                "SELECT id FROM ai_runs WHERE plan_id=? AND status IN ('queued','running','waiting','success','partial') "
                'ORDER BY created_at DESC LIMIT 1', (plan_id,)).first()
        if existing:
            return {'run_id': existing[0], 'replayed': True, **self.executor.status(existing[0], owner)}
        if plan['state'] != 'draft':
            raise HubError('AI_PLAN_ALREADY_RESOLVED', 409)
        if not self.executor.granted(plan['scope_id']):
            # Checked here as well as per step, so the person gets one clear reason instead of
            # an adopted-then-failed run. The per-step check stays as the revocation guard.
            raise HubError('AI_LOCAL_WRITE_NOT_GRANTED', 403)
        steps, created_paths, created_nodes = self._resolve(plan['steps'])
        if not steps:
            raise HubError('AI_PLAN_EMPTY', 422)
        run_id = self.executor.create_run(owner, steps, plan_id=plan_id)
        with connection(self.database, write=True) as con:
            con.exec_driver_sql("UPDATE ai_plans SET state='adopted',adopted_at=? WHERE id=?",
                                (utc_now(), plan_id))
        return self.executor.execute(run_id, plan['scope_id'], owner)

    def _resolve(self, plan_steps):
        """Turn `$created_path` / `$created_node` into concrete ids as the run progresses.

        The executor hands each step's result back, so this mapping is filled from real
        created ids, never from a guess. Steps that still contain an unresolved reference are
        dropped with an explicit reason.
        """
        resolved, paths, nodes, dropped = [], [], [], []
        for step in plan_steps:
            arguments = dict(step['arguments'])
            ok = True
            # Slot indices are 0-based positions in the list of *created* routes/nodes.
            # Counting before this step's own creation is what makes "$path" in step 1 mean
            # the route step 0 created, rather than an index one past the end.
            for field in ('path_id', 'goal_id', 'node_id'):
                value = arguments.get(field)
                if isinstance(value, dict) and '$created_path' in value:
                    arguments[field] = {'$path_slot': 0 if not paths else len(paths) - 1}
                elif isinstance(value, dict) and '$created_node' in value:
                    arguments[field] = {'$node_slot': int(value['$created_node'])}
            if step['capability_id'] == 'learning.path.create':
                paths.append(step['position'])
            if step['capability_id'] == 'learning.node.create':
                nodes.append(step['position'])
            if not ok:
                dropped.append(step['position'])
                continue
            resolved.append({'capability_id': step['capability_id'],
                             'capability_version': SPECS[step['capability_id']].version,
                             'arguments': arguments, 'plan_position': step['position']})
        return resolved, paths, nodes

    # ------------------------------------------------------------------- grants

    def grants(self):
        with connection(self.database) as con:
            rows = con.exec_driver_sql('SELECT * FROM ai_grants ORDER BY created_at DESC').mappings().all()
        now = utc_now()
        return [{'id': r['id'], 'kind': r['kind'], 'scope_id': r['scope_id'], 'scope_kind': r['scope_kind'],
                 'endpoint': r['endpoint'], 'model': r['model'], 'enabled': bool(r['enabled']),
                 'expires_at': r['expires_at'],
                 'active': bool(r['enabled']) and (not r['expires_at'] or r['expires_at'] > now)}
                for r in rows]

    def set_grant(self, kind, scope_id, enabled, days=None, endpoint='', model='', config_version=0):
        if kind not in ('local_write', 'outbound'):
            raise HubError('AI_GRANT_INVALID', 422)
        if kind == 'local_write' and not scope_id:
            raise HubError('AI_GRANT_INVALID', 422)
        if kind == 'outbound' and (not endpoint or not model):
            raise HubError('AI_GRANT_INVALID', 422)
        expires = (datetime.now().astimezone() + timedelta(days=days)).isoformat(timespec='seconds') if days else None
        now = utc_now()
        with connection(self.database, write=True) as con:
            row = con.exec_driver_sql('SELECT id FROM ai_grants WHERE kind=? AND scope_id IS ?',
                                      (kind, scope_id)).first()
            if row:
                con.exec_driver_sql(
                    'UPDATE ai_grants SET enabled=?,expires_at=?,endpoint=?,model=?,config_version=?,'
                    'updated_at=? WHERE id=?',
                    (int(enabled), expires, endpoint, model, config_version, now, row[0]))
                gid = row[0]
            else:
                gid = str(uuid4())
                con.exec_driver_sql(
                    'INSERT INTO ai_grants(id,kind,scope_id,scope_kind,endpoint,model,config_version,enabled,'
                    "expires_at,created_at,updated_at) VALUES(?,?,?,'learning_goal',?,?,?,?,?,?,?)",
                    (gid, kind, scope_id, endpoint, model, config_version, int(enabled), expires, now, now))
            if scope_id:
                activity(con, scope_id, 'granted' if enabled else 'revoked', ['ai'])
        return {'id': gid, 'kind': kind, 'scope_id': scope_id, 'enabled': bool(enabled), 'expires_at': expires}

    def outbound_allowed(self, endpoint, model, config_version):
        """Whether sending content to this exact endpoint+model is currently allowed."""
        with connection(self.database) as con:
            row = con.exec_driver_sql(
                'SELECT * FROM ai_grants WHERE kind=? AND endpoint=? AND enabled=1',
                ('outbound', endpoint)).mappings().first()
        if not row:
            return False
        if row['expires_at'] and row['expires_at'] <= utc_now():
            return False
        # A changed model or config version needs a fresh decision, not the old grant.
        return row['model'] == model and row['config_version'] == config_version

    def suspend_grants(self):
        """Called after a restore: an old authorization must not silently become active."""
        with connection(self.database, write=True) as con:
            con.exec_driver_sql("UPDATE ai_grants SET enabled=0,updated_at=? WHERE enabled=1", (utc_now(),))

    # -------------------------------------------------------------- preferences

    def preferences(self):
        with connection(self.database) as con:
            row = con.exec_driver_sql('SELECT * FROM ai_preferences WHERE id=1').mappings().first()
        return dict(row) if row else {**DEFAULT_PREFERENCES, 'version': 0}

    def save_preferences(self, payload):
        current = self.preferences()
        if payload.expected_version != current['version']:
            raise HubError('VERSION_CONFLICT')
        now = utc_now()
        values = (payload.daily_minutes, payload.session_minutes, payload.review_frequency,
                  payload.reminder_hour, payload.template_id, current['last_reviewed_at'],
                  current['version'] + 1, now)
        with connection(self.database, write=True) as con:
            con.exec_driver_sql(
                'INSERT INTO ai_preferences(id,daily_minutes,session_minutes,review_frequency,reminder_hour,'
                'template_id,last_reviewed_at,version,updated_at) VALUES(1,?,?,?,?,?,?,?,?) '
                'ON CONFLICT(id) DO UPDATE SET daily_minutes=excluded.daily_minutes,'
                'session_minutes=excluded.session_minutes,review_frequency=excluded.review_frequency,'
                'reminder_hour=excluded.reminder_hour,template_id=excluded.template_id,'
                'last_reviewed_at=excluded.last_reviewed_at,version=excluded.version,'
                'updated_at=excluded.updated_at', values)
        return self.preferences()

    # ---------------------------------------------------------------- templates

    def templates(self):
        with connection(self.database) as con:
            rows = con.exec_driver_sql('SELECT * FROM ai_templates ORDER BY created_at DESC').mappings().all()
        return [{'id': r['id'], 'name': r['name'], 'purpose': r['purpose'], 'version': r['version'],
                 'enabled': bool(r['enabled']), 'steps': json.loads(r['steps_json'])} for r in rows]

    def save_template(self, payload, template_id=None):
        steps = [s.model_dump() for s in payload.steps]
        for step in steps:
            spec = SPECS.get(step['capability_id'])
            if spec is None:
                raise HubError('CAPABILITY_UNKNOWN', 422)
            if set(step.get('arguments') or {}) - set(spec.fields):
                raise HubError('CAPABILITY_ARGUMENTS_UNKNOWN', 422)
        now = utc_now()
        with connection(self.database, write=True) as con:
            if template_id:
                row = con.exec_driver_sql('SELECT version FROM ai_templates WHERE id=?', (template_id,)).first()
                if not row:
                    raise HubError('LOCAL_RECORD_NOT_FOUND', 404)
                con.exec_driver_sql(
                    'UPDATE ai_templates SET name=?,purpose=?,version=?,enabled=?,steps_json=?,updated_at=? WHERE id=?',
                    (payload.name, payload.purpose, row[0] + 1, int(payload.enabled),
                     json.dumps(steps, ensure_ascii=False), now, template_id))
                return self._template(template_id)
            tid = str(uuid4())
            con.exec_driver_sql(
                "INSERT INTO ai_templates(id,name,purpose,version,enabled,steps_json,created_at,updated_at) "
                "VALUES(?,?,?,1,?,?,?,?)",
                (tid, payload.name, payload.purpose, int(payload.enabled),
                 json.dumps(steps, ensure_ascii=False), now, now))
        return self._template(tid)

    def _path_for(self, scope_id):
        """The first route under the selected goal, or None when the goal has none yet."""
        if not scope_id:
            return None
        with connection(self.database) as con:
            row = con.exec_driver_sql(
                'SELECT p.resource_id FROM learning_paths p JOIN resources r ON r.id=p.resource_id '
                "WHERE p.goal_id=? AND r.privacy_level='personal' AND r.deleted_at IS NULL "
                'ORDER BY p.resource_id LIMIT 1', (scope_id,)).first()
        return row[0] if row else None

    def _template(self, tid):
        return next((t for t in self.templates() if t['id'] == tid), None)

    def set_template_enabled(self, template_id, enabled):
        with connection(self.database, write=True) as con:
            if not con.exec_driver_sql('SELECT 1 FROM ai_templates WHERE id=?', (template_id,)).first():
                raise HubError('LOCAL_RECORD_NOT_FOUND', 404)
            con.exec_driver_sql('UPDATE ai_templates SET enabled=?,updated_at=? WHERE id=?',
                                (int(enabled), utc_now(), template_id))
        return self._template(template_id)

    def run_template(self, template_id, owner, scope_id, overrides=None):
        template = self._template(template_id)
        if not template or not template['enabled']:
            raise HubError('AI_TEMPLATE_DISABLED', 409)
        overrides = overrides or {}
        path_id = self._path_for(scope_id)
        steps = []
        if not path_id and scope_id:
            # The goal has no route yet. Creating one is itself a registered capability, so
            # the template's first step is an ordinary path creation, not a special case.
            steps.append({'capability_id': 'learning.path.create',
                          'capability_version': SPECS['learning.path.create'].version,
                          'arguments': {'goal_id': scope_id, 'title': template['name'][:60]}})
        for step in template['steps']:
            arguments = {**step['arguments'], **overrides.get(step['capability_id'], {})}
            # A template is written before any particular goal is chosen, so it refers to
            # "$path". Here that becomes a real route inside the selected goal.
            if arguments.get('path_id') == '$path':
                # No route existed, so step 0 created one; the node attaches to it.
                arguments['path_id'] = path_id or {'$path_slot': 0}
            node_ref = arguments.get('node_id')
            if isinstance(node_ref, str) and node_ref.startswith('$node'):
                # Templates use the same "$node0" shorthand the planner produces; translate
                # it to the slot the executor binds against real created ids.
                try:
                    arguments['node_id'] = {'$node_slot': int(node_ref[5:])}
                except ValueError:
                    raise HubError('AI_PLAN_REFERENCE_UNRESOLVED', 422) from None
            steps.append({'capability_id': step['capability_id'],
                          'capability_version': SPECS[step['capability_id']].version,
                          'arguments': arguments})
        run_id = self.executor.create_run(owner, steps, template_id=template_id)
        return self.executor.execute(run_id, scope_id, owner)

    # ------------------------------------------------------------------- sources

    def sources_for(self, scope_id, intent, limit=6, budget=18000):
        """Pick a small number of authorized sources to put in front of the model.

        Only learning-scope kinds are eligible, so finance, exercise, the vault and unrelated
        life tasks can never be pulled in — not by a search hit, not by a related task.
        """
        from .adapters import LEARNING_KINDS
        from ..knowledge.search import SearchService

        search = SearchService(self.database)
        words = ' '.join(str(intent or '').split()[:8])
        found = search.query(words, '', limit * 3, 0).get('items', []) if words else []
        picked, total = [], 0
        for item in found:
            if item.get('kind') not in LEARNING_KINDS:
                continue
            with connection(self.database) as con:
                try:
                    row = con.exec_driver_sql('SELECT * FROM resources WHERE id=?', (item['id'],)).mappings().first()
                    body, digest = search.source(con, row)
                except HubError:
                    continue
            text = body[:4000]
            if total + len(text) > budget:
                continue
            total += len(text)
            picked.append({'id': row['id'], 'kind': row['kind'], 'title': row['title'],
                           'version': row['version'], 'source_hash': digest, 'text': text})
            if len(picked) >= limit:
                break
        return picked

    # -------------------------------------------------------------------- review

    def review(self, scope_id, owner):
        """A review built only from records that exist.

        The requirement is explicit that an absence of evidence is reported as an absence, not
        turned into a claim about the person. So this returns computed facts and a list of open
        items; any wording the model adds is separated and marked as a suggestion.
        """
        from .stalled import StalledService

        goal = self.executor.adapters.learning.get('learning_goal', scope_id)
        paths = self.executor.adapters.learning.list('learning_path', limit=100)['items']
        mine = [p for p in paths if p.get('goal_id') == scope_id]
        nodes, tasks = [], []
        for path in mine:
            # `tree` already returns a flat, depth-ordered list, so no recursion is needed.
            nodes.extend(self.executor.adapters.learning.tree(path['id'])['nodes'])
        with connection(self.database) as con:
            rows = con.exec_driver_sql(
                'SELECT r.id,r.title,t.status,t.planned_date,t.completed_at '
                'FROM learning_node_tasks l JOIN resources r ON r.id=l.task_id JOIN tasks t ON t.resource_id=r.id '
                'JOIN knowledge_nodes n ON n.resource_id=l.node_id JOIN learning_paths p ON p.resource_id=n.path_id '
                "WHERE p.goal_id=? AND r.privacy_level='personal' AND r.deleted_at IS NULL "
                'ORDER BY r.created_at DESC LIMIT 200', (scope_id,)).mappings().all()
            tasks = [dict(r) for r in rows]
            assessments = con.exec_driver_sql(
                'SELECT count(*) FROM learning_assessments a JOIN knowledge_nodes n ON n.resource_id=a.node_id '
                'JOIN learning_paths p ON p.resource_id=n.path_id WHERE p.goal_id=?', (scope_id,)).scalar_one()
        done = [t for t in tasks if t['status'] == 'done']
        verified = [n for n in nodes if n.get('mastery') == 'verified']
        stalled = [row for row in StalledService(self.database).report()['items']
                   if row['kind'] in ('knowledge_node', 'learning_path', 'learning_goal')]
        facts = {
            'goals': 1 if goal else 0, 'paths': len(mine), 'nodes': len(nodes),
            'verified_nodes': len(verified), 'practice_tasks': len(tasks),
            'completed_tasks': len(done), 'assessments': int(assessments),
        }
        if not tasks and not assessments:
            observation = '目前没有记录到进展：这个目标下还没有练习任务或验收记录。'
        else:
            observation = (f'记录到 {len(tasks)} 项练习，其中 {len(done)} 项已完成；'
                           f'{len(nodes)} 个知识点中 {len(verified)} 个已验收。')
        return {
            'scope_id': scope_id,
            'facts': facts,
            'observation': observation,
            'stalled': stalled[:10],
            'next': self._next_steps(nodes, tasks),
            'notice': '这些是系统记录到的数据；系统不会据此判断你拖延或能力不足。',
        }

    def _next_steps(self, nodes, tasks):
        """Suggestions derived from state, each naming the fact it came from."""
        open_tasks = [t for t in tasks if t['status'] in ('todo', 'doing')]
        unverified = [n for n in nodes if n.get('mastery') != 'verified']
        steps = []
        if open_tasks:
            steps.append({'action': '继续练习', 'reason': f'有 {len(open_tasks)} 项练习未完成',
                          'task_id': open_tasks[0]['id'], 'title': open_tasks[0]['title']})
        if unverified:
            node = unverified[0]
            steps.append({'action': '验收或拆解', 'reason': '该知识点还没有验收记录',
                          'node_id': node.get('id'), 'title': node.get('title')})
        if not steps:
            steps.append({'action': '添加新的知识点', 'reason': '当前节点都已验收或无待办'})
        return steps

    # ------------------------------------------------------------------ schedule

    def schedule(self, scope_id, days):
        """Day-by-day plan of existing practice tasks, without creating duplicates.

        Each open practice task is placed on the next day that still has room under the daily
        budget. Nothing is created and no calendar event is made: this reuses the task's own
        planned date, which is what the rest of the app already reads.
        """
        prefs = self.preferences()
        per_day = max(1, prefs['daily_minutes'] // max(5, prefs['session_minutes']))
        with connection(self.database) as con:
            rows = con.exec_driver_sql(
                'SELECT r.id,r.title,t.status,t.planned_date '
                'FROM learning_node_tasks l JOIN resources r ON r.id=l.task_id JOIN tasks t ON t.resource_id=r.id '
                'JOIN knowledge_nodes n ON n.resource_id=l.node_id JOIN learning_paths p ON p.resource_id=n.path_id '
                "WHERE p.goal_id=? AND r.privacy_level='personal' AND r.deleted_at IS NULL "
                "AND t.status IN ('todo','doing') ORDER BY r.created_at LIMIT 200", (scope_id,)).mappings().all()
        start = _today()
        slots = {start + timedelta(days=i): [] for i in range(days)}
        for row in rows:
            for day in slots:
                if len(slots[day]) < per_day:
                    slots[day].append({'task_id': row['id'], 'title': row['title'],
                                       'current': row['planned_date']})
                    break
        return {
            'session_minutes': prefs['session_minutes'],
            'per_day': per_day,
            'days': [{'date': day.isoformat(), 'items': items} for day, items in slots.items() if items],
            'notice': '这只列出已有练习任务的时间安排，不会新建任务；需要时到任务页调整计划日。',
        }


    def review_slot(self):
        """The current review slot key, or None when reviews are off.

        One slot per day or per ISO week; a run for a slot is recorded so a restart does not
        produce a second review for the same period.
        """
        prefs = self.preferences()
        if prefs['review_frequency'] == 'off':
            return None
        today = _today()
        if prefs['review_frequency'] == 'daily':
            return f'day-{today.isoformat()}'
        year, week, _ = today.isocalendar()
        return f'week-{year}-{week:02d}'

    def claim_review(self, slot, owner, run_id=None):
        """Take the slot for this period, or report that it is already taken.

        The slot row is the record that a period was reviewed. It may reference a run when a
        review produced one; a purely local report has no run, and NULL says so honestly
        rather than pointing at an id that does not exist.
        """
        with connection(self.database, write=True) as con:
            prior = con.exec_driver_sql('SELECT slot FROM ai_review_slots WHERE slot=?', (slot,)).first()
            if prior:
                return None
            con.exec_driver_sql('INSERT INTO ai_review_slots(slot,run_id,created_at) VALUES(?,?,?)',
                                (slot, run_id, utc_now()))
        return slot
