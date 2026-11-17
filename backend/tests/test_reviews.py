"""Acting on what stopped moving: a decision with a consequence, and history left intact."""
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from backend.app.core.config import Settings
from backend.app.core.database import HubError, connection
from backend.app.modules.knowledge.reviews import ReviewService
from backend.app.modules.knowledge.stalled import StalledService
from backend.app.modules.learning.schemas import Goal, Node, PathWrite
from backend.app.modules.learning.service import LearningService
from backend.app.modules.life.tasks.schemas import TaskCreate, TaskPatch
from backend.app.modules.life.tasks.service import TaskService
from backend.app.modules.projects.schemas import ProjectWrite
from backend.app.modules.projects.service import ProjectService
from backend.app.modules.knowledge.router import ReviewWrite
from backend.manage import initialize_empty
from backend.maintenance import upgrade


def key(): return str(uuid4())
def ago(days): return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec='microseconds').replace('+00:00', 'Z')
def ahead(days): return (date.today() + timedelta(days=days)).isoformat()


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.settings = Settings(data_root=self.root / 'hub', legacy_database=self.root / 'legacy', reminders_enabled=False)
        initialize_empty(self.settings.data_root); upgrade(self.settings)
        self.db = self.settings.database
        self.tasks = TaskService(self.db)
        self.learning = LearningService(self.db)
        self.projects = ProjectService(self.db)
        self.stalled = StalledService(self.db)
        self.reviews = ReviewService(self.db)

    def tearDown(self): self.tmp.cleanup()

    # ------------------------------------------------------------------ helpers

    def version(self, rid):
        with connection(self.db) as c:
            return c.exec_driver_sql('SELECT version FROM resources WHERE id=?', (rid,)).scalar_one()

    def row(self, rid):
        with connection(self.db) as c:
            return dict(c.exec_driver_sql('SELECT * FROM resources WHERE id=?', (rid,)).mappings().one())

    def age(self, rid, days):
        with connection(self.db, write=True) as c:
            c.exec_driver_sql('UPDATE resources SET updated_at=?, created_at=? WHERE id=?', (ago(days), ago(days), rid))

    def stall_task(self, title='搁置的任务', days=30):
        made = self.tasks.create(TaskCreate(title=title), key())[0]
        self.age(made['id'], days)
        return made['id']

    def decide(self, rid, decision, **kw):
        payload = ReviewWrite(expected_version=self.version(rid), decision=decision, **kw)
        return self.reviews.record_decision(rid, payload, key())

    def titles(self): return [row['title'] for row in self.stalled.report()['items']]

    # -------------------------------------------------------------------- tests

    def test_finishing_a_stale_task_removes_it_and_keeps_the_review(self):
        rid = self.stall_task()
        self.assertEqual(self.titles(), ['搁置的任务'])
        result = self.decide(rid, 'done', note='做完了')
        with connection(self.db) as c:
            self.assertEqual(c.exec_driver_sql('SELECT status FROM tasks WHERE resource_id=?', (rid,)).scalar_one(), 'done')
        self.assertEqual(self.titles(), [], 'a finished task is no longer stalled')
        self.assertEqual(result['review']['decision'], 'done')
        self.assertEqual(self.reviews.recent()['items'][0]['note'], '做完了')

    def test_a_deferral_hides_the_item_until_its_date_without_touching_updated_at(self):
        rid = self.stall_task()
        before = self.row(rid)
        self.decide(rid, 'snooze', next_date=ahead(21))
        after = self.row(rid)
        self.assertEqual(self.titles(), [], 'a deferred item waits instead of nagging')
        self.assertEqual(after['updated_at'], before['updated_at'],
                         'deferring must not make a stale thing look freshly touched')
        self.assertEqual(after['version'], before['version'])

    def test_a_past_deferral_comes_back_into_view(self):
        rid = self.stall_task()
        # Write the review, then move its date into the past: the item must reappear.
        self.decide(rid, 'snooze', next_date=ahead(5))
        self.assertEqual(self.titles(), [])
        with connection(self.db, write=True) as c:
            c.exec_driver_sql("UPDATE stalled_reviews SET next_date=? WHERE resource_id=?", ((date.today() - timedelta(days=1)).isoformat(), rid))
        self.assertEqual(self.titles(), ['搁置的任务'], 'once the date passes, it is back on the list')

    def test_continuing_a_task_reschedules_it_and_the_clock_restarts_honestly(self):
        rid = self.stall_task()
        target = ahead(3)
        self.decide(rid, 'continue', next_date=target)
        with connection(self.db) as c:
            self.assertEqual(c.exec_driver_sql('SELECT planned_date FROM tasks WHERE resource_id=?', (rid,)).scalar_one(), target)
        self.assertEqual(self.titles(), [], 'a rescheduled task restarts its quiet clock')
        item = self.reviews.recent()['items'][0]
        self.assertEqual(item['decision'], 'continue')
        self.assertEqual(item['next_date'], target)

    def test_dropping_a_task_cancels_it(self):
        rid = self.stall_task()
        self.decide(rid, 'drop')
        with connection(self.db) as c:
            self.assertEqual(c.exec_driver_sql('SELECT status FROM tasks WHERE resource_id=?', (rid,)).scalar_one(), 'cancelled')
        self.assertEqual(self.titles(), [])

    def test_a_date_is_refused_where_it_would_mean_nothing(self):
        rid = self.stall_task()
        with self.assertRaises(HubError) as caught:
            self.decide(rid, 'done', next_date=ahead(5))
        self.assertEqual(caught.exception.code, 'REVIEW_DATE_NOT_ALLOWED')
        with self.assertRaises(HubError) as caught:
            self.decide(rid, 'continue', next_date=(date.today() - timedelta(days=1)).isoformat())
        self.assertEqual(caught.exception.code, 'REVIEW_DATE_NOT_FUTURE')

    def test_a_deferral_without_a_date_uses_the_kinds_own_rhythm(self):
        rid = self.stall_task()   # tasks go quiet after 7 days
        result = self.decide(rid, 'snooze')
        self.assertEqual(result['review']['next_date'], ahead(7),
                         'a task is asked about again after its own quiet period')

    def test_a_learning_node_cannot_be_ticked_done_without_an_assessment(self):
        goal = self.learning.save('learning_goal', Goal(title='目标', acceptance_criteria='标准'), key())
        path = self.learning.save('learning_path', PathWrite(title='路线', goal_id=goal['id']), key())
        node = self.learning.save('knowledge_node', Node(title='卡住的知识点', path_id=path['id'], acceptance_criteria='能复现'), key())
        self.age(node['id'], 40)
        with self.assertRaises(HubError) as caught:
            self.decide(node['id'], 'done')
        self.assertEqual(caught.exception.code, 'REVIEW_DONE_NEEDS_ASSESSMENT',
                         'verification has a real procedure; a review button must not fake it')

    def test_dropping_a_node_puts_it_in_the_recycle_bin(self):
        goal = self.learning.save('learning_goal', Goal(title='目标', acceptance_criteria='标准'), key())
        path = self.learning.save('learning_path', PathWrite(title='路线', goal_id=goal['id']), key())
        node = self.learning.save('knowledge_node', Node(title='不学了', path_id=path['id'], acceptance_criteria='x'), key())
        self.age(node['id'], 40)
        self.decide(node['id'], 'drop')
        with connection(self.db) as c:
            self.assertIsNotNone(c.exec_driver_sql('SELECT deleted_at FROM resources WHERE id=?', (node['id'],)).scalar_one())
        self.assertEqual(self.titles(), [])

    def test_a_project_can_be_finished_or_rescheduled(self):
        project = self.projects.save(ProjectWrite(title='旧项目', status='active'), key())
        self.age(project['id'], 40)
        self.assertEqual(self.titles(), ['旧项目'])
        self.decide(project['id'], 'continue', next_date=ahead(10))
        with connection(self.db) as c:
            got = dict(c.exec_driver_sql('SELECT status,target_date FROM projects WHERE resource_id=?', (project['id'],)).mappings().one())
        self.assertEqual(got['status'], 'active')
        self.assertEqual(got['target_date'], ahead(10))
        self.assertEqual(self.titles(), [])
        self.decide(project['id'], 'done')
        with connection(self.db) as c:
            self.assertEqual(c.exec_driver_sql('SELECT status FROM projects WHERE resource_id=?', (project['id'],)).scalar_one(), 'completed')

    def test_the_same_request_twice_records_one_review(self):
        rid = self.stall_task()
        payload = ReviewWrite(expected_version=self.version(rid), decision='snooze')
        shared = key()
        first = self.reviews.record_decision(rid, payload, shared)
        second = self.reviews.record_decision(rid, payload, shared)
        self.assertEqual(first['review']['id'], second['review']['id'])
        self.assertEqual(len(self.reviews.recent()['items']), 1, 'a retried request must not double-record')

    def test_the_history_keeps_a_dropped_item(self):
        rid = self.stall_task()
        self.decide(rid, 'drop')
        items = self.reviews.recent()['items']
        self.assertEqual(len(items), 1, 'deciding to drop something is still a decision worth keeping')
        self.assertEqual(items[0]['decision'], 'drop')
        self.assertFalse(items[0]['in_trash'], 'a cancelled task stays in the ledger, not the bin')

    def test_a_dropped_node_is_reported_as_being_in_the_bin(self):
        goal = self.learning.save('learning_goal', Goal(title='目标', acceptance_criteria='标准'), key())
        path = self.learning.save('learning_path', PathWrite(title='路线', goal_id=goal['id']), key())
        node = self.learning.save('knowledge_node', Node(title='不学了', path_id=path['id'], acceptance_criteria='x'), key())
        self.age(node['id'], 40)
        self.decide(node['id'], 'drop')
        item = self.reviews.recent()['items'][0]
        self.assertTrue(item['in_trash'], 'a set-aside node is in the recycle bin, and the history says so')

    def test_a_stale_version_is_refused(self):
        rid = self.stall_task()
        payload = ReviewWrite(expected_version=self.version(rid) + 5, decision='snooze')
        with self.assertRaises(HubError) as caught:
            self.reviews.record_decision(rid, payload, key())
        self.assertEqual(caught.exception.code, 'VERSION_CONFLICT')

    def test_the_report_carries_the_last_decision(self):
        rid = self.stall_task()
        self.decide(rid, 'snooze', next_date=ahead(1), note='下周再说')
        with connection(self.db, write=True) as c:
            c.exec_driver_sql("UPDATE stalled_reviews SET next_date=? WHERE resource_id=?", ((date.today() - timedelta(days=1)).isoformat(), rid))
        item = self.stalled.report()['items'][0]
        self.assertEqual(item['last_decision'], 'snooze')
        self.assertEqual(item['note'], '下周再说')

    def test_a_private_resource_can_not_be_reviewed(self):
        rid = self.stall_task()
        with connection(self.db, write=True) as c:
            c.exec_driver_sql("UPDATE resources SET privacy_level='private' WHERE id=?", (rid,))
        payload = ReviewWrite(expected_version=self.version(rid), decision='snooze')
        with self.assertRaises(HubError) as caught:
            self.reviews.record_decision(rid, payload, key())
        self.assertEqual(caught.exception.status, 404, 'a hidden thing must not be reachable by id either')


if __name__ == '__main__':
    unittest.main()
