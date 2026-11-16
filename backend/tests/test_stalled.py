"""The stalled view: what stopped moving, counted from the last real change."""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from backend.app.core.config import Settings
from backend.app.core.database import connection
from backend.app.modules.knowledge.stalled import StalledService
from backend.app.modules.learning.schemas import Goal, Node, PathWrite
from backend.app.modules.learning.service import LearningService
from backend.app.modules.life.tasks.schemas import TaskCreate, TaskPatch
from backend.app.modules.life.tasks.service import TaskService
from backend.manage import initialize_empty
from backend.maintenance import upgrade


def key(): return str(uuid4())
def ago(days): return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec='microseconds').replace('+00:00', 'Z')


class StalledTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.settings = Settings(data_root=self.root / 'hub', legacy_database=self.root / 'legacy', reminders_enabled=False)
        initialize_empty(self.settings.data_root); upgrade(self.settings)
        self.db = self.settings.database
        self.tasks = TaskService(self.db)
        self.learning = LearningService(self.db)
        self.service = StalledService(self.db)

    def tearDown(self): self.tmp.cleanup()

    def version(self, rid):
        with connection(self.db) as c:
            return c.exec_driver_sql('SELECT version FROM resources WHERE id=?', (rid,)).scalar_one()

    def age(self, rid, days):
        with connection(self.db, write=True) as c:
            c.exec_driver_sql('UPDATE resources SET updated_at=?, created_at=? WHERE id=?', (ago(days), ago(days), rid))

    def titles(self, **kw): return [row['title'] for row in self.service.report(**kw)['items']]

    def test_a_task_only_appears_once_it_has_been_quiet_past_the_threshold(self):
        fresh = self.tasks.create(TaskCreate(title='刚建的'), key())[0]
        old = self.tasks.create(TaskCreate(title='搁置很久'), key())[0]
        self.age(old['id'], 30)
        self.assertEqual(self.titles(), ['搁置很久'], 'a task younger than 7 days is not stalled')
        self.assertIn('刚建的', self.titles(threshold_days=0), 'an explicit threshold overrides the default')

    def test_finished_and_trashed_tasks_are_not_stalled(self):
        done = self.tasks.create(TaskCreate(title='已完成'), key())[0]
        self.tasks.change(done['id'], TaskPatch(expected_version=self.version(done['id']), status='done'))
        self.age(done['id'], 60)
        gone = self.tasks.create(TaskCreate(title='已在回收站'), key())[0]
        self.age(gone['id'], 60)
        self.tasks.trash(gone['id'], self.version(gone['id']))
        self.assertEqual(self.titles(), [])

    def test_a_private_task_is_never_listed(self):
        hidden = self.tasks.create(TaskCreate(title='私密的'), key())[0]
        self.age(hidden['id'], 60)
        with connection(self.db, write=True) as c:
            c.exec_driver_sql("UPDATE resources SET privacy_level='private' WHERE id=?", (hidden['id'],))
        self.assertEqual(self.titles(), [])
        self.assertEqual(self.titles(threshold_days=0), [], 'no threshold may reveal it')

    def test_every_query_states_the_privacy_rule_itself(self):
        """The lineage join already hides private rows; the explicit clause is the second guard.
        Removing it would still pass a behavioural test, so assert the rule is in the query."""
        import inspect
        from backend.app.modules.knowledge import stalled
        source = inspect.getsource(stalled)
        self.assertEqual(source.count("r.privacy_level='personal'"), 3,
                         'tasks, learning and projects must each filter privacy explicitly')

    def test_a_learning_node_counts_practice_as_movement(self):
        goal = self.learning.save('learning_goal', Goal(title='目标', acceptance_criteria='标准'), key())
        path = self.learning.save('learning_path', PathWrite(title='路线', goal_id=goal['id']), key())
        node = self.learning.save('knowledge_node', Node(title='卡住的知识点', path_id=path['id'], acceptance_criteria='能复现'), key())
        self.age(node['id'], 40)
        self.assertEqual(self.titles(), ['卡住的知识点'])
        row = self.service.report()['items'][0]
        self.assertEqual(row['basis'], '创建后未练习')

        # A practice session after the node was created makes the node recent again.
        with connection(self.db, write=True) as c:
            c.exec_driver_sql('INSERT INTO learning_sessions VALUES(?,?,?,?,?,?,?)',
                              (key(), node['id'], ago(2), ago(2), 600, None, ago(2)))
        self.assertEqual(self.titles(), [], 'recent practice means the node is not stalled')
        self.assertEqual(self.service.report(threshold_days=0)['items'][0]['basis'], '最后练习或验收')

    def test_a_verified_node_and_a_trashed_goal_tree_are_excluded(self):
        goal = self.learning.save('learning_goal', Goal(title='目标', acceptance_criteria='标准'), key())
        path = self.learning.save('learning_path', PathWrite(title='路线', goal_id=goal['id']), key())
        node = self.learning.save('knowledge_node', Node(title='已验收', path_id=path['id'], acceptance_criteria='x'), key())
        with connection(self.db, write=True) as c:
            c.exec_driver_sql("UPDATE knowledge_nodes SET mastery='verified' WHERE resource_id=?", (node['id'],))
        self.age(node['id'], 90)
        self.assertEqual(self.titles(), [])
        with connection(self.db, write=True) as c:
            c.exec_driver_sql("UPDATE resources SET deleted_at=? WHERE id=?", (ago(1), path['id']))
        self.assertEqual(self.titles(), [], 'a node under a trashed path is hidden')

    def test_results_are_ordered_longest_quiet_first_and_carry_a_basis(self):
        for title, days in (('三天', 10), ('最久', 90), ('中间', 30)):
            made = self.tasks.create(TaskCreate(title=title), key())[0]
            self.age(made['id'], days)
        report = self.service.report()
        self.assertEqual([row['title'] for row in report['items']], ['最久', '中间', '三天'])
        self.assertTrue(all(row['basis'] for row in report['items']))
        self.assertTrue(all(row['destination'] for row in report['items']))
        self.assertIn('不是截止日期', report['notice'])


if __name__ == '__main__':
    unittest.main()
