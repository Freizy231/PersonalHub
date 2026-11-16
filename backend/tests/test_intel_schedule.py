"""Per-source queries, the collection schedule and inline trend data.

No network: the collectors are replaced, and the worker's tick is called directly.
"""
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from backend.app.core.config import Settings
from backend.app.core.database import HubError, connection
from backend.app.modules.intelligence.collectors import with_query
from backend.app.modules.intelligence.schemas import ScheduleWrite, SourceWrite
from backend.app.modules.intelligence.service import IntelligenceService
from backend.app.modules.intelligence.worker import ScheduleWorker
from backend.manage import initialize_empty
from backend.maintenance import upgrade


def key(): return str(uuid4())


class IntelScheduleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.settings = Settings(data_root=self.root / 'hub', legacy_database=self.root / 'legacy', reminders_enabled=False)
        initialize_empty(self.settings.data_root); upgrade(self.settings)
        self.service = IntelligenceService(self.settings)

    def tearDown(self): self.tmp.cleanup()

    def source(self, **kw):
        payload = SourceWrite(**{'kind': 'arxiv', 'name': '来源', **kw})
        return self.service.source_save(payload, key())

    def test_the_schedule_starts_disabled_and_nothing_is_due(self):
        schedule = self.service.schedule()
        self.assertFalse(schedule['enabled'])
        self.source(enabled=True)
        self.assertEqual(self.service.due_sources(datetime(2026, 11, 16, 22, 0).astimezone()), [],
                         'a disabled schedule never collects')

    def test_enabling_the_schedule_makes_a_source_due_once_per_slot(self):
        self.source(enabled=True)
        self.service.schedule_save(ScheduleWrite(enabled=True, runs_per_day=2,
                                                 local_hours=['09:00', '21:00'], expected_version=0), key())
        now = datetime(2026, 11, 16, 22, 30).astimezone()   # after the 21:00 slot
        due = self.service.due_sources(now)
        self.assertEqual(len(due), 1)
        slot = due[0]['slot']
        self.service.mark_scheduled(due[0]['id'], slot)
        self.assertEqual(self.service.due_sources(now), [], 'the same slot never runs twice')

    def test_a_slot_that_has_not_arrived_yet_uses_yesterdays_last_run(self):
        now = datetime(2026, 11, 16, 8, 0).astimezone()   # before the 09:00 slot
        slot = self.service.current_slot(self.service.schedule(), now)
        self.assertEqual(slot, (now - timedelta(days=1)).replace(hour=21, minute=0, second=0, microsecond=0)
                         .isoformat(timespec='seconds'))

    def test_a_disabled_source_is_skipped(self):
        self.source(enabled=False)
        self.service.schedule_save(ScheduleWrite(enabled=True, runs_per_day=1,
                                                 local_hours=['09:00'], expected_version=0), key())
        self.assertEqual(self.service.due_sources(datetime(2026, 11, 16, 12, 0).astimezone()), [])

    def test_the_worker_does_nothing_when_the_schedule_is_off(self):
        worker = ScheduleWorker(self.settings)
        import asyncio
        self.assertEqual(asyncio.run(worker.tick()), [])
        self.assertEqual(worker.state, 'disabled')

    def test_schedule_validation_rejects_mismatched_or_duplicate_slots(self):
        for bad in (dict(runs_per_day=2, local_hours=['09:00']),
                    dict(runs_per_day=2, local_hours=['09:00', '09:00']),
                    dict(runs_per_day=1, local_hours=['9:00']),
                    dict(runs_per_day=25, local_hours=['09:00'])):
            with self.assertRaises(ValidationError, msg=bad):
                ScheduleWrite(enabled=True, expected_version=0, **bad)

    def test_a_source_keeps_its_own_query(self):
        created = self.source(query='cat:cs.CR', enabled=True)
        self.assertEqual(created['query'], 'cat:cs.CR')
        self.assertEqual(self.service.sources()['items'][0]['query'], 'cat:cs.CR')
        with self.assertRaises(ValidationError):
            SourceWrite(kind='arxiv', name='x', query='bad\x00query')

    def test_the_query_reaches_only_the_adapter_that_accepts_one(self):
        arxiv = with_query({'kind': 'arxiv', 'endpoint': 'https://export.arxiv.org/api/query', 'query': 'cat:cs.CR'})
        self.assertIn('search_query=cat:cs.CR', arxiv)
        feed = with_query({'kind': 'rss', 'endpoint': 'https://example.com/feed.xml', 'query': 'ignored'})
        self.assertEqual(feed, 'https://example.com/feed.xml', 'an RSS endpoint is the address the person saved')
        empty = with_query({'kind': 'arxiv', 'endpoint': 'https://export.arxiv.org/api/query', 'query': ''})
        self.assertEqual(empty, 'https://export.arxiv.org/api/query')

    def test_the_list_carries_a_trend_only_when_snapshots_exist(self):
        with connection(self.settings.database, write=True) as c:
            now = datetime(2026, 11, 16, 9, 0, tzinfo=None)
            for index, (stars, forks) in enumerate(((10, 2), (14, 3), (21, 5))):
                c.exec_driver_sql(
                    'INSERT INTO intel_items(id,source_key,remote_id,title,url,target_key,summary,version,created_at,updated_at) '
                    "VALUES(?,?,?,?,?,?,?,1,?,?)",
                    (f'item-{index}', 'github', f'r{index}', f'仓库 {index}', 'https://example.com/' + str(index),
                     'https://example.com/' + str(index), 'summary', now.isoformat(), now.isoformat()))
            c.exec_driver_sql(
                'INSERT INTO intel_snapshots(id,item_id,run_id,captured_at,raw_json,metrics_json,raw_available,dedup_key) '
                "VALUES('s1','item-0',NULL,?,'{}',?,1,'d1'),('s2','item-0',NULL,?,'{}',?,1,'d2')",
                ((now - timedelta(days=2)).isoformat(), '{"stars":10,"forks":2}',
                 now.isoformat(), '{"stars":21,"forks":5}'))
        items = {i['id']: i for i in self.service.list()['items']}
        self.assertEqual(items['item-0']['trend']['stars'], [10, 21])
        self.assertEqual(items['item-0']['trend']['forks'], [2, 5])
        self.assertEqual(items['item-1']['trend']['stars'], [], 'a single snapshot is not a trend')
        self.assertEqual(items['item-1']['trend']['series'], 0)


if __name__ == '__main__':
    unittest.main()
