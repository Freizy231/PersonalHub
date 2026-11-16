"""Permanent removal of one trashed resource, with a preview first.

Only an ordinary, already-trashed resource can be purged. The rule everywhere else
in the Hub is soft delete; this is the single, explicit exception, and it is why the
caller must first ask for a preview that names every consequence:

* rows in the resource's extension table (and rows that hang off it),
* cross-resource links pointing at it,
* files on disk (Markdown bodies and revisions, library blobs and extracted text),
* whether a dependent fact blocks the removal (an event that references the task,
  a meal that references the recipe, a practice task pinned to a node).

A blocked removal is refused wholesale: nothing is deleted and nothing on disk is
touched. The database row is the authority; files are removed only after the
transaction commits, and a failed unlink leaves an orphan file rather than a
dangling row.
"""
from pathlib import Path

from ...core.database import HubError, connection, utc_now
from ...core.note_files import NoteFiles, file_lock, parts
from ..library.documents.storage import BlobFiles, blob_parts
from .service import EXTENSIONS, rows, visible

# Tables reached through the resource's own extension row (FK -> resources, CASCADE).
# Order matters: children before parents.
OWNED = {
    'note': (('note_revisions', 'note_id'), ('note_links', 'source_id')),
    'document': (('document_bookmarks', 'document_id'),),
    'collection': (('collection_members', 'collection_id'),),
    'task': (),
    'event': (),
    'project': (('project_entries', 'project_id'), ('project_milestones', 'project_id'), ('project_tasks', 'project_id')),
    'recipe': (('recipe_ingredients', 'recipe_id'), ('meal_dishes', 'recipe_id')),
    'learning_path': (('learning_reviews', 'path_id'),),
    'knowledge_node': (('assessment_evidence', 'resource_id'),),
    'learning_goal': (),
    'book': (),
    'bookmark': (),
    'course': (),
}

# References from another resource that RESTRICT deletion. Removing the subject would
# silently drop these facts, so the purge is refused until the person detaches them.
# (table, column, kinds this applies to, message)
BLOCKERS = (
    ('events', 'task_id', ('task',), '有一条日程关联了这个任务'),
    ('exercise_plans', 'task_id', ('task',), '有一项运动计划关联了这个任务'),
    ('exercise_plans', 'event_id', ('event',), '有一项运动计划关联了这条日程'),
    ('learning_node_tasks', 'task_id', ('task',), '有一条学习练习使用了这个任务'),
    ('meal_dishes', 'recipe_id', ('recipe',), '有一餐使用这个食谱'),
    ('meal_plans', 'recipe_id', ('recipe',), '有一餐安排使用这个食谱'),
    ('project_entries', 'note_id', ('note',), '这条笔记仍是某个项目的日志'),
    ('learning_reviews', 'note_id', ('note',), '这条笔记仍是某个路线的复盘'),
    ('learning_sessions', 'reflection_note_id', ('note',), '这条笔记仍是某次学习的复盘'),
)

LABELS = {'task': '任务', 'event': '日程', 'note': '笔记', 'document': '文献', 'book': '书籍',
          'bookmark': '网页', 'course': '课程', 'collection': '集合', 'project': '项目',
          'recipe': '食谱', 'learning_goal': '学习目标', 'learning_path': '学习路线',
          'knowledge_node': '知识点'}


class PurgeService:
    def __init__(self, database, data_root):
        self.database = database
        self.root = Path(data_root)

    def _owned_files(self, con, kind, rid):
        """Files whose removal belongs to this resource alone."""
        files = []
        if kind == 'note':
            note = con.exec_driver_sql('SELECT body_path,content_sha256 FROM notes WHERE resource_id=?', (rid,)).first()
            if note:
                files.append(note[0])
            for row in con.exec_driver_sql('SELECT body_path FROM note_revisions WHERE note_id=?', (rid,)):
                # Revisions of a purged note go with it; the same path is never shared across notes.
                files.append(row[0])
        if kind == 'document':
            document = con.exec_driver_sql('SELECT blob_id,extracted_text_path FROM documents WHERE resource_id=?', (rid,)).first()
            if document:
                blob = con.exec_driver_sql('SELECT relative_path FROM blobs WHERE id=?', (document[0],)).first()
                if blob:
                    files.append(blob[0])
                if document[1]:
                    files.append(document[1])
        return list(dict.fromkeys(f for f in files if f))

    def _blockers(self, con, kind, rid):
        found = []
        for table, column, kinds, message in BLOCKERS:
            if kind not in kinds:
                continue
            count = con.exec_driver_sql(f'SELECT count(*) FROM {table} WHERE {column}=?', (rid,)).scalar_one()
            if count:
                found.append({'reason': message, 'count': count})
        if kind in ('learning_goal', 'learning_path', 'knowledge_node'):
            # Deleting an ancestor would strand descendants that are still in use.
            column, table = ('goal_id', 'learning_paths') if kind == 'learning_goal' else \
                ('path_id', 'knowledge_nodes') if kind == 'learning_path' else ('parent_id', 'knowledge_nodes')
            count = con.exec_driver_sql(
                f'SELECT count(*) FROM {table} x JOIN resources r ON r.id=x.resource_id '
                f'WHERE x.{column}=? AND r.deleted_at IS NULL', (rid,)).scalar_one()
            if count:
                found.append({'reason': '仍有下级内容引用它（且未在回收站）', 'count': count})
        if kind == 'document':
            shared = con.exec_driver_sql(
                'SELECT count(*) FROM documents other WHERE other.blob_id=('
                'SELECT blob_id FROM documents WHERE resource_id=?) AND other.resource_id<>?',
                (rid, rid)).scalar_one()
            if shared:
                found.append({'reason': '这个文件仍被其他文献引用', 'count': shared})
        return found

    def preview(self, rid):
        with connection(self.database) as con:
            r = visible(con, rid, trash=True)
            if not r['deleted_at']:
                raise HubError('PURGE_NOT_TRASHED', 409)
            kind = r['kind']
            if kind not in EXTENSIONS or kind not in OWNED:
                raise HubError('PURGE_UNSUPPORTED', 422)
            blockers = self._blockers(con, kind, rid)
            related = con.exec_driver_sql(
                'SELECT count(*) FROM resource_relations WHERE source_id=? OR target_id=?', (rid, rid)).scalar_one()
            tags = con.exec_driver_sql('SELECT count(*) FROM resource_tags WHERE resource_id=?', (rid,)).scalar_one()
            files = self._owned_files(con, kind, rid)
            return {
                'id': rid, 'kind': kind, 'kind_label': LABELS.get(kind, kind), 'title': r['title'],
                'version': r['version'], 'trashed_at': r['deleted_at'],
                'blockers': blockers,
                'deletable': not blockers,
                'removes': {'relations': related, 'tags': tags,
                            'revisions': con.exec_driver_sql('SELECT count(*) FROM note_revisions WHERE note_id=?', (rid,)).scalar_one() if kind == 'note' else 0},
                'files': files,
                'file_count': len(files),
                'notice': '删除后不可恢复，也不进入备份；请确认已不再需要它。',
            }

    def purge(self, rid, expected_version):
        files = []
        blob = None
        with file_lock(self.root), connection(self.database, write=True) as con:
            r = visible(con, rid, trash=True)
            if not r['deleted_at']:
                raise HubError('PURGE_NOT_TRASHED', 409)
            if r['version'] != expected_version:
                raise HubError('VERSION_CONFLICT')
            kind = r['kind']
            if kind not in EXTENSIONS or kind not in OWNED:
                raise HubError('PURGE_UNSUPPORTED', 422)
            blockers = self._blockers(con, kind, rid)
            if blockers:
                raise HubError('PURGE_BLOCKED', 409)
            files = self._owned_files(con, kind, rid)
            # Explicit, ordered cleanup. Cascades are not relied on: the upgrade audit
            # and older databases cannot be assumed to have them enabled everywhere.
            table = EXTENSIONS[kind]
            for child, column in OWNED[kind]:
                con.exec_driver_sql(f'DELETE FROM {child} WHERE {column}=?', (rid,))
            if kind in ('learning_path', 'knowledge_node'):
                con.exec_driver_sql('DELETE FROM learning_node_tasks WHERE node_id=?', (rid,)) if kind == 'knowledge_node' else None
            if kind == 'knowledge_node':
                con.exec_driver_sql('DELETE FROM learning_assessments WHERE node_id=?', (rid,))
                con.exec_driver_sql('DELETE FROM learning_sessions WHERE node_id=?', (rid,))
            if kind == 'task':
                con.exec_driver_sql('DELETE FROM learning_node_tasks WHERE task_id=?', (rid,))
                con.exec_driver_sql('DELETE FROM project_tasks WHERE task_id=?', (rid,))
            if kind in ('task', 'event'):
                con.exec_driver_sql('DELETE FROM reminders WHERE resource_id=?', (rid,))
            if kind == 'document':
                # Remember the blob before its owning row disappears; a blob row without a
                # document (or a document without a blob) would be a dangling fact.
                blob = con.exec_driver_sql('SELECT blob_id FROM documents WHERE resource_id=?', (rid,)).scalar_one_or_none()
                con.exec_driver_sql('DELETE FROM extraction_jobs WHERE document_id=?', (rid,))
                con.exec_driver_sql('DELETE FROM launch_history WHERE document_id=?', (rid,))
                con.exec_driver_sql('DELETE FROM launch_previews WHERE document_id=?', (rid,))
                con.exec_driver_sql('DELETE FROM document_imports WHERE document_id=?', (rid,))
            con.exec_driver_sql('DELETE FROM resource_relations WHERE source_id=? OR target_id=?', (rid, rid))
            con.exec_driver_sql('DELETE FROM resource_tags WHERE resource_id=?', (rid,))
            con.exec_driver_sql('DELETE FROM note_links WHERE target_id=?', (rid,))
            con.exec_driver_sql('DELETE FROM activities WHERE resource_id=?', (rid,))
            con.exec_driver_sql('DELETE FROM search_fts WHERE resource_id=?', (rid,))
            con.exec_driver_sql('DELETE FROM search_entries WHERE resource_id=?', (rid,))
            con.exec_driver_sql(f'DELETE FROM {table} WHERE resource_id=?', (rid,))
            if kind == 'document' and blob:
                # Only when nothing else points at it; identical uploads dedupe to one document,
                # but an older database could still share a blob row.
                if not con.exec_driver_sql('SELECT 1 FROM documents WHERE blob_id=? LIMIT 1', (blob,)).first():
                    con.exec_driver_sql('DELETE FROM blobs WHERE id=?', (blob,))
            con.exec_driver_sql('DELETE FROM resources WHERE id=?', (rid,))
        # Files go last: a committed delete with an orphan file is recoverable by hand;
        # a removed file with a live row is not.
        removed, failed = [], []
        notes, blobs = NoteFiles(self.root), BlobFiles(self.root)
        for relative in files:
            try:
                # Library files live under library/, note bodies under notes/; each has its own path guard.
                notes.unlink(relative, blob_parts if relative.startswith('library/') else parts)
                removed.append(relative)
            except (OSError, HubError):
                failed.append(relative)
        return {'id': rid, 'status': 'purged', 'files_removed': len(removed), 'files_failed': failed}
