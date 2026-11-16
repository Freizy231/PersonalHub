"""Permanent removal from the trash: preview first, refuse when referenced, files last."""
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from backend.app.core.config import Settings
from backend.app.core.database import HubError, connection
from backend.app.modules.knowledge.purge import PurgeService
from backend.app.modules.knowledge.service import KnowledgeService
from backend.app.modules.knowledge.router import CollectionWrite
from backend.app.modules.learning.schemas import Goal, Node, PathWrite, Practice
from backend.app.modules.learning.service import LearningService
from backend.app.modules.library.notes.schemas import NoteCreate
from backend.app.modules.library.notes.service import NoteService
from backend.app.modules.life.tasks.schemas import TaskCreate, TaskVersion
from backend.app.modules.life.tasks.service import TaskService
from backend.manage import initialize_empty
from backend.maintenance import upgrade


def key(): return str(uuid4())


class PurgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.settings = Settings(data_root=self.root / 'hub', legacy_database=self.root / 'legacy', reminders_enabled=False)
        initialize_empty(self.settings.data_root); upgrade(self.settings)
        self.db = self.settings.database
        self.purge = PurgeService(self.db, self.settings.data_root)
        self.notes = NoteService(self.db, self.settings.data_root)
        self.tasks = TaskService(self.db)
        self.learning = LearningService(self.db)

    def tearDown(self): self.tmp.cleanup()

    def trashed_note(self):
        note, _ = self.notes.create(NoteCreate(title='要删掉的笔记', body='正文内容'), key())
        self.notes.trash(note['id'], note['version'])
        with connection(self.db) as c:
            row = c.exec_driver_sql('SELECT body_path,version FROM notes n JOIN resources r ON r.id=n.resource_id WHERE n.resource_id=?', (note['id'],)).one()
        return note['id'], row[0], row[1]

    def test_preview_refuses_a_resource_that_is_not_in_the_trash(self):
        task, _ = self.tasks.create(TaskCreate(title='还在用'), key())
        with self.assertRaises(HubError) as raised:
            self.purge.preview(task['id'])
        self.assertEqual(raised.exception.code, 'PURGE_NOT_TRASHED')

    def test_purging_a_trashed_note_removes_rows_index_and_file(self):
        rid, body_path, version = self.trashed_note()
        with connection(self.db) as c:
            self.assertTrue(c.exec_driver_sql('SELECT 1 FROM notes WHERE resource_id=?', (rid,)).first())
        self.assertTrue((self.settings.data_root / body_path).is_file())

        preview = self.purge.preview(rid)
        self.assertTrue(preview['deletable'], preview)
        self.assertIn(body_path, preview['files'])

        result = self.purge.purge(rid, version)
        self.assertEqual(result['files_removed'], 1)
        self.assertEqual(result['files_failed'], [])
        with connection(self.db) as c:
            for table, column in (('resources', 'id'), ('notes', 'resource_id'), ('note_revisions', 'note_id'),
                                  ('search_entries', 'resource_id'), ('activities', 'resource_id'),
                                  ('resource_tags', 'resource_id'), ('note_links', 'source_id')):
                self.assertEqual(c.exec_driver_sql(f'SELECT count(*) FROM {table} WHERE {column}=?', (rid,)).scalar_one(), 0, table)
        self.assertFalse((self.settings.data_root / body_path).exists())

    def test_a_stale_version_is_refused_and_nothing_is_removed(self):
        rid, body_path, version = self.trashed_note()
        with self.assertRaises(HubError) as raised:
            self.purge.purge(rid, version + 5)
        self.assertEqual(raised.exception.code, 'VERSION_CONFLICT')
        self.assertTrue((self.settings.data_root / body_path).is_file())

    def test_a_task_referenced_by_an_event_blocks_removal(self):
        # The event is attached first, then the task is trashed: a live event keeps its subject.
        task, _ = self.tasks.create(TaskCreate(title='被日程引用'), key())
        from backend.app.modules.life.events.service import EventService
        from backend.app.modules.life.events.schemas import EventCreate
        EventService(self.db).create(EventCreate(title='日程', is_all_day=True, start_date='2026-11-12',
                                                 end_date='2026-11-13', timezone='Asia/Shanghai', task_id=task['id']), key())
        self.tasks.trash(task['id'], task['version'])
        blocked = self.purge.preview(task['id'])
        self.assertFalse(blocked['deletable'])
        self.assertTrue(any('日程' in b['reason'] for b in blocked['blockers']), blocked)
        with self.assertRaises(HubError) as raised:
            self.purge.purge(task['id'], blocked['version'])
        self.assertEqual(raised.exception.code, 'PURGE_BLOCKED')
        with connection(self.db) as c:
            self.assertTrue(c.exec_driver_sql('SELECT 1 FROM resources WHERE id=?', (task['id'],)).first())

    def test_an_unreferenced_trashed_task_is_deletable(self):
        task, _ = self.tasks.create(TaskCreate(title='没人引用'), key())
        self.tasks.trash(task['id'], task['version'])
        preview = self.purge.preview(task['id'])
        self.assertTrue(preview['deletable'], preview)
        self.assertEqual(self.purge.purge(task['id'], preview['version'])['status'], 'purged')

    def test_a_knowledge_node_with_a_live_child_cannot_be_purged(self):
        goal = self.learning.save('learning_goal', Goal(title='目标', acceptance_criteria='标准'), key())
        path = self.learning.save('learning_path', PathWrite(title='路线', goal_id=goal['id']), key())
        parent = self.learning.save('knowledge_node', Node(title='父节点', path_id=path['id'], acceptance_criteria='x'), key())
        child = self.learning.save('knowledge_node', Node(title='子节点', path_id=path['id'], parent_id=parent['id'], acceptance_criteria='y'), key())
        # Trash the child first: trashing a parent with a live child is already refused upstream.
        self.learning.trash('knowledge_node', child['id'], child['version'])
        self.learning.trash('knowledge_node', parent['id'], parent['version'])
        with connection(self.db, write=True) as c:
            c.exec_driver_sql('UPDATE resources SET deleted_at=NULL WHERE id=?', (child['id'],))
        preview = self.purge.preview(parent['id'])
        self.assertFalse(preview['deletable'], 'a live child still points at the trashed parent')
        with connection(self.db, write=True) as c:
            c.exec_driver_sql('UPDATE resources SET deleted_at=? WHERE id=?', ('2026-11-01T00:00:00Z', child['id']))
        self.assertTrue(self.purge.preview(parent['id'])['deletable'])

    def test_purging_a_trashed_document_removes_its_blob_row_and_file(self):
        """Identical uploads dedupe to one document, so a purged document always owns its file."""
        from backend.app.modules.library.documents.service import DocumentService
        import hashlib
        import os
        documents = DocumentService(self.db)
        data = b'%PDF-1.7\n' + b'x' * 200
        iid = key(); relative, fd = documents.begin(iid, 'pdf')
        with os.fdopen(fd, 'wb') as handle:
            handle.write(data); handle.flush(); os.fsync(handle.fileno())
        created = documents.commit(iid, relative, len(data), hashlib.sha256(data).hexdigest(), 'a.pdf', key(), 'pdf')
        rid = created['document']['id']
        with connection(self.db) as c:
            blob_path = c.exec_driver_sql('SELECT b.relative_path FROM documents d JOIN blobs b ON b.id=d.blob_id WHERE d.resource_id=?', (rid,)).scalar_one()
        documents.trash(rid, created['document']['version'])
        preview = self.purge.preview(rid)
        self.assertTrue(preview['deletable'], preview)
        self.assertIn(blob_path, preview['files'])
        self.assertEqual(self.purge.purge(rid, preview['version'])['files_removed'], 1)
        self.assertFalse((self.settings.data_root / blob_path).exists())
        with connection(self.db) as c:
            self.assertEqual(c.exec_driver_sql('SELECT count(*) FROM documents WHERE resource_id=?', (rid,)).scalar_one(), 0)
            self.assertEqual(c.exec_driver_sql('SELECT count(*) FROM blobs').scalar_one(), 0)


if __name__ == '__main__':
    unittest.main()
