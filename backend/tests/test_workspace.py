"""The AI workspace: scope refusal, adoption, idempotency, grants and review honesty."""
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from uuid import uuid4

from backend.app.core.capabilities import CapabilityError
from backend.app.core.config import Settings
from backend.app.core.database import HubError, connection
from backend.app.modules.knowledge.executor import Executor
from backend.app.modules.knowledge.planner import parse_plan
from backend.app.modules.knowledge.workspace import WorkspaceService
from backend.app.modules.learning.schemas import Goal, Node, PathWrite
from backend.app.modules.learning.service import LearningService
from backend.app.modules.life.tasks.schemas import TaskCreate
from backend.app.modules.life.tasks.service import TaskService
from backend.manage import initialize_empty
from backend.maintenance import upgrade


def key(): return str(uuid4())


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.settings = Settings(data_root=self.root / 'hub', legacy_database=self.root / 'legacy',
                                 reminders_enabled=False)
        initialize_empty(self.settings.data_root); upgrade(self.settings)
        self.db = self.settings.database
        self.learning = LearningService(self.db)
        self.tasks = TaskService(self.db)
        self.workspace = WorkspaceService(self.settings)
        self.executor = Executor(self.settings)
        self.goal = self.learning.save('learning_goal', Goal(title='RAG 项目', acceptance_criteria='能跑起来'), key())
        self.path = self.learning.save('learning_path', PathWrite(title='基础', goal_id=self.goal['id']), key())

    def tearDown(self): self.tmp.cleanup()

    def count(self, table, where='1=1', args=()):
        with connection(self.db) as c:
            return c.exec_driver_sql(f'SELECT count(*) FROM {table} WHERE {where}', args).scalar_one()

    def grant(self, scope=None, enabled=True):
        return self.workspace.set_grant('local_write', scope or self.goal['id'], enabled)

    def plan_from(self, steps, scope=None):
        """Draft a plan directly from a validated step list (no model call involved)."""
        text = json.dumps({'title': '四周 RAG 计划', 'rationale': '按你的基础与时间安排',
                           'steps': steps}, ensure_ascii=False)
        return self.workspace.draft_plan(text, None, scope or self.goal['id'], 'test-model')

    # ------------------------------------------------------------------ planning

    def test_a_plan_with_an_unknown_capability_is_refused_without_writing(self):
        steps = [{'capability_id': 'system.shell', 'arguments': {'cmd': 'rm -rf /'}},
                 {'capability_id': 'learning.node.create',
                  'arguments': {'path_id': self.path['id'], 'title': '向量检索', 'acceptance_criteria': '能说明原理'}}]
        plan = self.plan_from(steps)
        self.assertEqual(len(plan['steps']), 1, 'only the declared capability survives')
        self.assertEqual(plan['rejected'][0]['code'], 'CAPABILITY_UNKNOWN')
        self.assertEqual(self.count('knowledge_nodes'), 0, 'drafting writes no business data')

    def test_a_plan_whose_output_cannot_be_parsed_writes_nothing(self):
        with self.assertRaises(CapabilityError):
            self.workspace.draft_plan('好的，这是我的计划：我认为你应该先学 Python。', None, self.goal['id'])
        self.assertEqual(self.count('ai_plans'), 0)

    def test_a_step_with_an_undeclared_field_is_rejected(self):
        steps = [{'capability_id': 'learning.node.create',
                  'arguments': {'path_id': self.path['id'], 'title': '向量检索',
                                'acceptance_criteria': '能说明原理', 'execute_sql': 'DROP TABLE resources'}}]
        with self.assertRaises(CapabilityError) as caught:
            self.plan_from(steps)
        self.assertEqual(caught.exception.code, 'AI_PLAN_EMPTY',
                         'a plan whose every step was refused is not a plan')
        self.assertEqual(self.count('knowledge_nodes'), 0)

    def test_a_node_without_acceptance_criteria_is_rejected(self):
        steps = [{'capability_id': 'learning.node.create',
                  'arguments': {'path_id': self.path['id'], 'title': '向量检索'}}]
        with self.assertRaises(CapabilityError):
            self.plan_from(steps)
        self.assertEqual(self.count('knowledge_nodes'), 0,
                         'a node with no finish line can never be verified')

    # ------------------------------------------------------------------ adoption

    def test_adopting_a_plan_creates_real_records_that_the_app_can_open(self):
        self.grant()
        steps = [
            {'capability_id': 'learning.node.create',
             'arguments': {'path_id': '$path', 'title': '向量检索', 'acceptance_criteria': '能解释 embedding 距离'}},
            {'capability_id': 'learning.practice.create',
             'arguments': {'node_id': '$node0', 'title': '实现最小检索', 'description': '用 numpy 写余弦相似度'}},
        ]
        # A path reference needs a path created in the same plan; use the real id here.
        steps[0]['arguments']['path_id'] = self.path['id']
        steps[1]['arguments']['node_id'] = {'$created_node': 0}
        plan = self.plan_from(steps)
        self.assertEqual(len(plan['steps']), 2,
                         'a reference to a node created earlier in the same plan is valid as a draft')
        result = self.workspace.adopt(plan['id'], 'owner-1')
        self.assertEqual(result['status'], 'success')
        node_ids = result['steps'][0]['resource_ids']
        self.assertEqual(len(node_ids), 1)
        with connection(self.db) as c:
            node = c.exec_driver_sql('SELECT * FROM resources WHERE id=?', (node_ids[0],)).mappings().one()
        self.assertEqual(node['kind'], 'knowledge_node')
        self.assertEqual(node['title'], '向量检索')

    def test_the_practice_step_uses_the_node_the_same_plan_created(self):
        """A plan is written before anything exists; the practice step must bind at run time."""
        self.grant()
        steps = [
            {'capability_id': 'learning.node.create',
             'arguments': {'path_id': self.path['id'], 'title': '分块与嵌入',
                           'acceptance_criteria': '能说明 chunk 大小的影响'}},
            {'capability_id': 'learning.practice.create',
             'arguments': {'node_id': '$node0', 'title': '切一份文档并嵌入'}},
        ]
        plan = self.plan_from(steps)
        self.assertEqual(len(plan['steps']), 2)
        result = self.workspace.adopt(plan['id'], 'owner-1')
        self.assertEqual(result['status'], 'success',
                         'the second step should bind to the node the first step created')
        node_id = result['steps'][0]['resource_ids'][0]
        task_id = result['steps'][1]['resource_ids'][0]
        with connection(self.db) as c:
            linked = c.exec_driver_sql('SELECT node_id FROM learning_node_tasks WHERE task_id=?',
                                       (task_id,)).scalar_one()
        self.assertEqual(linked, node_id, 'the practice task must be attached to that node')

    def test_adopting_the_same_plan_twice_returns_the_first_run(self):
        self.grant()
        steps = [{'capability_id': 'learning.node.create',
                  'arguments': {'path_id': self.path['id'], 'title': '向量检索', 'acceptance_criteria': 'x'}}]
        plan = self.plan_from(steps)
        first = self.workspace.adopt(plan['id'], 'owner-1')
        second = self.workspace.adopt(plan['id'], 'owner-1')
        self.assertEqual(first['id'], second['id'], 're-adopting must not create a second set of records')
        self.assertEqual(self.count('knowledge_nodes'), 1)

    def test_a_second_run_directly_does_not_duplicate_work(self):
        """The per-step idempotency key is stable, so the same step cannot create twice."""
        self.grant()
        node = self.learning.save('knowledge_node', Node(title='节点', path_id=self.path['id'],
                                                         acceptance_criteria='x'), key())
        step = {'capability_id': 'learning.practice.create',
                'capability_version': 1, 'arguments': {'node_id': node['id'], 'title': '练习'}}
        run = self.executor.create_run('owner-1', [step])
        self.executor.execute(run, self.goal['id'], 'owner-1')
        before = self.count('tasks')
        with connection(self.db, write=True) as c:
            # Replay the very same step record, as a crash-and-resume would.
            c.exec_driver_sql("UPDATE ai_run_steps SET status='pending' WHERE run_id=?", (run,))
            c.exec_driver_sql("UPDATE ai_runs SET status='queued' WHERE id=?", (run,))
        self.executor.execute(run, self.goal['id'], 'owner-1')
        self.assertEqual(self.count('tasks'), before, 'a retried step must not create a second task')

    def test_writing_without_a_grant_is_refused(self):
        plan = self.plan_from([{'capability_id': 'learning.node.create',
                                'arguments': {'path_id': self.path['id'], 'title': '节点',
                                              'acceptance_criteria': 'x'}}])
        result = self.workspace.adopt(plan['id'], 'owner-1')
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['error_code'], 'CAPABILITY_SCOPE_DENIED')
        self.assertEqual(self.count('knowledge_nodes'), 0)

    def test_revoking_a_grant_mid_run_stops_the_next_step(self):
        self.grant()
        node = self.learning.save('knowledge_node', Node(title='节点', path_id=self.path['id'],
                                                         acceptance_criteria='x'), key())
        steps = [{'capability_id': 'notes.create', 'capability_version': 1,
                  'arguments': {'title': '第一条'}},
                 {'capability_id': 'learning.practice.create', 'capability_version': 1,
                  'arguments': {'node_id': node['id'], 'title': '练习'}}]
        run = self.executor.create_run('owner-1', steps)
        # Revoke between creating the run and executing it, as a person could.
        self.grant(enabled=False)
        result = self.executor.execute(run, self.goal['id'], 'owner-1')
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(self.count('notes'), 0, 'nothing was written after the revocation')

    # -------------------------------------------------------------------- scope

    def test_a_node_outside_the_authorized_goal_is_refused(self):
        other = self.learning.save('learning_goal', Goal(title='别的目标', acceptance_criteria='y'), key())
        other_path = self.learning.save('learning_path', PathWrite(title='别处', goal_id=other['id']), key())
        foreign = self.learning.save('knowledge_node', Node(title='外部节点', path_id=other_path['id'],
                                                           acceptance_criteria='z'), key())
        self.grant()
        plan = self.plan_from([{'capability_id': 'learning.practice.create',
                                'arguments': {'node_id': foreign['id'], 'title': '越界练习'}}])
        result = self.workspace.adopt(plan['id'], 'owner-1')
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['error_code'], 'CAPABILITY_SCOPE_DENIED')
        self.assertEqual(self.count('tasks'), 0)

    def test_finance_and_exercise_are_never_available_as_sources(self):
        with connection(self.db) as c:
            c.exec_driver_sql("SELECT 1")  # tables exist; the filter is by kind
        sources = self.workspace.sources_for(self.goal['id'], '预算 支出')
        for source in sources:
            self.assertIn(source['kind'], {'learning_goal', 'learning_path', 'knowledge_node', 'note',
                                           'document', 'book', 'bookmark', 'course'})

    # -------------------------------------------------------------------- read

    def test_related_practice_tasks_are_readable_inside_the_goal(self):
        node = self.learning.save('knowledge_node', Node(title='节点', path_id=self.path['id'],
                                                         acceptance_criteria='x'), key())
        with connection(self.db) as c:
            version = c.exec_driver_sql('SELECT version FROM resources WHERE id=?', (node['id'],)).scalar_one()
        from backend.app.modules.learning.schemas import Practice
        self.learning.practice(node['id'], Practice(expected_version=version, title='练习一'), key())
        result = self.executor.adapters.calls['tasks.related']({'node_id': node['id']}, self.goal['id'], key())
        self.assertEqual(len(result.data['items']), 1)

    # ---------------------------------------------------------------- preferences

    def test_preferences_are_versioned(self):
        from backend.app.modules.knowledge.workspace_schemas import PreferencesWrite
        saved = self.workspace.save_preferences(PreferencesWrite(
            daily_minutes=20, session_minutes=25, review_frequency='daily', reminder_hour=20,
            expected_version=0))
        self.assertEqual(saved['daily_minutes'], 20)
        with self.assertRaises(HubError) as caught:
            self.workspace.save_preferences(PreferencesWrite(
                daily_minutes=30, session_minutes=25, review_frequency='daily', reminder_hour=20,
                expected_version=0))
        self.assertEqual(caught.exception.code, 'VERSION_CONFLICT')

    def test_review_reports_an_absence_instead_of_a_judgement(self):
        review = self.workspace.review(self.goal['id'], 'owner-1')
        self.assertIn('没有记录到进展', review['observation'])
        self.assertNotIn('拖延', review['observation'])
        self.assertEqual(review['facts']['practice_tasks'], 0)

    def test_review_counts_only_real_records(self):
        node = self.learning.save('knowledge_node', Node(title='节点', path_id=self.path['id'],
                                                         acceptance_criteria='x'), key())
        with connection(self.db) as c:
            version = c.exec_driver_sql('SELECT version FROM resources WHERE id=?', (node['id'],)).scalar_one()
        from backend.app.modules.learning.schemas import Practice
        self.learning.practice(node['id'], Practice(expected_version=version, title='练习'), key())
        review = self.workspace.review(self.goal['id'], 'owner-1')
        self.assertEqual(review['facts']['practice_tasks'], 1)
        self.assertIn('1 项练习', review['observation'])

    # ------------------------------------------------------------------ schedule

    def test_scheduling_reuses_existing_tasks_and_creates_none(self):
        node = self.learning.save('knowledge_node', Node(title='节点', path_id=self.path['id'],
                                                         acceptance_criteria='x'), key())
        with connection(self.db) as c:
            version = c.exec_driver_sql('SELECT version FROM resources WHERE id=?', (node['id'],)).scalar_one()
        from backend.app.modules.learning.schemas import Practice
        self.learning.practice(node['id'], Practice(expected_version=version, title='练习'), key())
        before = self.count('tasks')
        plan = self.workspace.schedule(self.goal['id'], 7)
        self.assertEqual(self.count('tasks'), before, 'a schedule proposal must not create tasks')
        self.assertEqual(len(plan['days']), 1)
        self.assertEqual(plan['days'][0]['date'], date.today().isoformat())

    # ---------------------------------------------------------------- templates

    def test_a_template_can_be_saved_reused_and_disabled(self):
        from backend.app.modules.knowledge.workspace_schemas import TemplateStep, TemplateWrite
        payload = TemplateWrite(name='二十五分钟精读', purpose='论文精读', steps=[
            TemplateStep(capability_id='notes.create', arguments={'title': '精读问题'})])
        template = self.workspace.save_template(payload)
        self.assertEqual(template['version'], 1)
        with self.assertRaises(HubError):
            self.workspace.save_template(TemplateWrite(name='x', steps=[
                TemplateStep(capability_id='system.shell', arguments={})]))
        self.workspace.set_template_enabled(template['id'], False)
        with self.assertRaises(HubError) as caught:
            self.workspace.run_template(template['id'], 'owner-1', self.goal['id'])
        self.assertEqual(caught.exception.code, 'AI_TEMPLATE_DISABLED')


if __name__ == '__main__':
    unittest.main()


class TemplateTests(unittest.TestCase):
    """The second flow: paper reading, built only from registered capabilities."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.settings = Settings(data_root=self.root / 'hub', legacy_database=self.root / 'legacy',
                                 reminders_enabled=False)
        initialize_empty(self.settings.data_root); upgrade(self.settings)
        self.db = self.settings.database
        self.learning = LearningService(self.db)
        self.workspace = WorkspaceService(self.settings)
        self.goal = self.learning.save('learning_goal', Goal(title='读论文', acceptance_criteria='能讲清'), key())
        self.workspace.set_grant('local_write', self.goal['id'], True)

    def tearDown(self): self.tmp.cleanup()

    def count(self, table):
        with connection(self.db) as c:
            return c.exec_driver_sql(f'SELECT count(*) FROM {table}').scalar_one()

    def test_the_paper_flow_is_a_template_not_a_kernel_branch(self):
        from backend.app.modules.knowledge import templates
        steps = templates.paper_reading()
        ids = {s['capability_id'] for s in steps}
        from backend.app.modules.knowledge.adapters import SPECS
        self.assertTrue(ids <= set(SPECS), 'every template step is a registered capability')
        source = Path(templates.__file__).read_text(encoding='utf-8')
        self.assertNotIn('if paper', source.lower(), 'paper logic must not live in the kernel')

    def test_running_the_paper_template_creates_a_node_practice_and_notes(self):
        from backend.app.modules.knowledge.templates import ensure_default_templates
        made = ensure_default_templates(self.db)
        self.assertTrue(made, 'the two starting templates are installed once')
        template = next(t for t in self.workspace.templates() if t['name'].startswith('论文精读'))
        result = self.workspace.run_template(template['id'], 'owner-1', self.goal['id'])
        self.assertEqual(result['status'], 'success')
        self.assertEqual(self.count('knowledge_nodes'), 1)
        self.assertGreaterEqual(self.count('tasks'), 2, 'one reading block and one review')
        self.assertGreaterEqual(self.count('notes'), 2, 'the question list and the reading note')

    def test_a_template_can_be_copied_and_adjusted_then_reused(self):
        from backend.app.modules.knowledge.templates import ensure_default_templates
        from backend.app.modules.knowledge.workspace_schemas import TemplateStep, TemplateWrite
        ensure_default_templates(self.db)
        base = next(t for t in self.workspace.templates() if t['name'].startswith('论文精读'))
        mine = self.workspace.save_template(TemplateWrite(
            name='我的二十五分钟精读', purpose=base['purpose'],
            steps=[TemplateStep(capability_id=s['capability_id'], arguments=s['arguments'])
                   for s in base['steps']]))
        self.assertEqual(mine['version'], 1)
        first = self.workspace.run_template(mine['id'], 'owner-1', self.goal['id'])
        self.assertEqual(first['status'], 'success')
        nodes_before = self.count('knowledge_nodes')
        self.workspace.set_template_enabled(mine['id'], False)
        with self.assertRaises(HubError):
            self.workspace.run_template(mine['id'], 'owner-1', self.goal['id'])
        self.assertEqual(self.count('knowledge_nodes'), nodes_before, 'a disabled template runs nothing')

    def test_a_goal_with_no_route_gets_one_created_as_a_normal_step(self):
        from backend.app.modules.knowledge.templates import ensure_default_templates
        ensure_default_templates(self.db)
        template = next(t for t in self.workspace.templates() if t['name'].startswith('学习推进'))
        result = self.workspace.run_template(template['id'], 'owner-1', self.goal['id'])
        self.assertEqual(result['status'], 'success')
        self.assertEqual(self.count('learning_paths'), 1)


class ReviewScheduleTests(unittest.TestCase):
    """Scheduled reviews run once per period and never fire without a grant."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.settings = Settings(data_root=self.root / 'hub', legacy_database=self.root / 'legacy',
                                 reminders_enabled=False)
        initialize_empty(self.settings.data_root); upgrade(self.settings)
        self.db = self.settings.database
        self.learning = LearningService(self.db)
        self.workspace = WorkspaceService(self.settings)
        self.goal = self.learning.save('learning_goal', Goal(title='复盘目标', acceptance_criteria='x'), key())

    def tearDown(self): self.tmp.cleanup()

    def worker(self):
        from backend.app.modules.knowledge.review_worker import ReviewWorker
        return ReviewWorker(self.settings)

    def test_no_review_is_produced_without_a_grant(self):
        result = self.worker().tick()
        self.assertEqual(result.get('reason'), 'no active grant')
        self.assertNotIn('reports', result)

    def test_one_period_produces_one_review_even_across_restarts(self):
        from backend.app.modules.knowledge.workspace_schemas import PreferencesWrite
        self.workspace.set_grant('local_write', self.goal['id'], True)
        self.workspace.save_preferences(PreferencesWrite(
            daily_minutes=20, session_minutes=25, review_frequency='weekly', reminder_hour=20,
            expected_version=0))
        first = self.worker().tick()
        self.assertEqual(len(first['reports']), 1)
        # A second process (or a restart) must find the slot already used.
        second = self.worker().tick()
        self.assertEqual(second['reason'], 'already reviewed')
        self.assertNotIn('reports', second)

    def test_reviews_off_means_no_slot_at_all(self):
        from backend.app.modules.knowledge.workspace_schemas import PreferencesWrite
        self.workspace.set_grant('local_write', self.goal['id'], True)
        self.workspace.save_preferences(PreferencesWrite(
            daily_minutes=20, session_minutes=25, review_frequency='off', reminder_hour=20,
            expected_version=0))
        self.assertEqual(self.worker().tick()['reason'], 'reviews off')


class ConversationTests(unittest.TestCase):
    """Conversation bodies are managed files; sources are versioned for staleness."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.settings = Settings(data_root=self.root / 'hub', legacy_database=self.root / 'legacy',
                                 reminders_enabled=False)
        initialize_empty(self.settings.data_root); upgrade(self.settings)
        self.db = self.settings.database
        self.learning = LearningService(self.db)
        self.workspace = WorkspaceService(self.settings)
        self.goal = self.learning.save('learning_goal', Goal(title='目标', acceptance_criteria='x'), key())
        self.conv = self.workspace.start_conversation(self.goal['id'], '对话', 'owner-1')

    def tearDown(self): self.tmp.cleanup()

    def test_message_bodies_live_in_the_snapshot_scope_not_in_sqlite(self):
        self.workspace.append_message(self.conv['id'], 'user', '我想学 RAG')
        with connection(self.db) as c:
            row = c.exec_driver_sql('SELECT body_path FROM ai_messages').mappings().one()
        self.assertTrue(row['body_path'].startswith('notes/'), 'managed files live under notes/')
        from backend.app.core.note_snapshot import NoteFiles
        inventory = NoteFiles(self.root / 'hub').inventory()
        self.assertIn(row['body_path'], inventory, 'conversation bodies are part of the snapshot')

    def test_editing_a_source_marks_the_conversation_stale(self):
        note = self.workspace.executor.adapters.notes.create(
            __import__('backend.app.modules.library.notes.schemas', fromlist=['NoteCreate'])
            .NoteCreate(title='资料', body='原始内容'), key())[0]
        ref = {'id': note['id'], 'version': note['version'], 'source_hash': 'a' * 64}
        self.workspace.append_message(self.conv['id'], 'user', '基于资料提问',
                                     [note['id']], 'user', [ref])
        self.assertEqual(self.workspace.conversation(self.conv['id'], 'owner-1')['stale_sources'], [])
        with connection(self.db, write=True) as c:
            c.exec_driver_sql('UPDATE resources SET version=version+1 WHERE id=?', (note['id'],))
        loaded = self.workspace.conversation(self.conv['id'], 'owner-1')
        self.assertIn(note['id'], loaded['stale_sources'])
        self.assertIn('不能作为当前依据', loaded['stale_notice'])
        self.assertTrue(loaded['messages'][0]['stale'])

    def test_clearing_a_conversation_removes_its_files(self):
        self.workspace.append_message(self.conv['id'], 'user', '内容')
        from backend.app.core.note_snapshot import NoteFiles
        self.assertNotEqual(NoteFiles(self.root / 'hub').inventory(), {})
        self.workspace.clear_conversation(self.conv['id'])
        with connection(self.db) as c:
            self.assertEqual(c.exec_driver_sql('SELECT count(*) FROM ai_messages').scalar_one(), 0)
        self.assertEqual(NoteFiles(self.root / 'hub').inventory(), {}, 'no orphan body files')
