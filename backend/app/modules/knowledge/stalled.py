"""What has been left behind: a read-only view of things that stopped moving.

This answers a different question from "what is due". A task can be months from its
deadline and still be the thing you keep avoiding; a learning node can have no deadline
at all and still be stuck. So the signal is time since the last real change, not a date.

Only ordinary, visible facts are counted. Anything private, trashed, or belonging to a
trashed ancestor is excluded by the same rules the rest of the app uses, so this page can
never become a way to notice something that is meant to be hidden.

Nothing here writes, and no figure is estimated: if a record has never moved, its age is
measured from its creation, and the response says so in `basis`.
"""
from datetime import date, datetime, timezone

from ...core.database import connection
from .service import lineage_sql

# A thing is "stalled" once the threshold has passed. Chosen per kind because the natural
# rhythm differs: a task sitting for a week is suspicious, a project for a week is not.
THRESHOLDS = {'task': 7, 'knowledge_node': 14, 'learning_path': 21, 'learning_goal': 30,
              'project': 14, 'note': 30}

LABELS = {'task': '任务', 'knowledge_node': '知识点', 'learning_path': '学习路线',
          'learning_goal': '学习目标', 'project': '项目', 'note': '笔记'}

DESTINATIONS = {'task': '/tasks', 'knowledge_node': '/learning', 'learning_path': '/learning',
                'learning_goal': '/learning', 'project': '/projects', 'note': '/notes'}


def _parse(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return None


def _days_since(instant, now):
    moment = _parse(instant)
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return max(0, (now - moment).days)


class StalledService:
    def __init__(self, database):
        self.database = database

    def report(self, threshold_days=None, limit=200):
        now = datetime.now(timezone.utc)
        found = []
        with connection(self.database) as con:
            found += self._tasks(con, now)
            found += self._learning(con, now)
            found += self._projects(con, now)
        # Only what has actually gone quiet, longest quiet first.
        found = [row for row in found
                 if row['days'] >= (threshold_days if threshold_days is not None else THRESHOLDS.get(row['kind'], 14))]
        found.sort(key=lambda row: (-row['days'], row['title']))
        return {
            'items': found[:limit],
            'total': len(found),
            'as_of': now.isoformat(timespec='seconds').replace('+00:00', 'Z'),
            'thresholds': THRESHOLDS,
            'notice': '按最后变动时间计算，不是截止日期。私密库与回收站不在其中。',
        }

    def _tasks(self, con, now):
        rows = con.exec_driver_sql(
            "SELECT r.id,r.title,r.updated_at,r.created_at,t.status,t.priority,t.planned_date,t.due_date,t.due_at "
            "FROM resources r JOIN tasks t ON t.resource_id=r.id "
            "WHERE r.kind='task' AND r.privacy_level='personal' AND r.deleted_at IS NULL "
            "AND t.status IN ('todo','doing') AND " + lineage_sql('r')).mappings()
        result = []
        for row in rows:
            # For an untouched task, creation is the last movement; say which one is used.
            days = _days_since(row['updated_at'] or row['created_at'], now)
            if days is None:
                continue
            result.append({
                'id': row['id'], 'kind': 'task', 'kind_label': LABELS['task'], 'title': row['title'],
                'days': days, 'since': row['updated_at'] or row['created_at'],
                'basis': '最后修改' if row['updated_at'] != row['created_at'] else '创建后未修改',
                'detail': self._task_detail(row), 'destination': DESTINATIONS['task'],
            })
        return result

    @staticmethod
    def _task_detail(row):
        parts = []
        if row['status'] == 'doing':
            parts.append('进行中')
        if row['priority'] == 3:
            parts.append('高优先级')
        if row['planned_date']:
            parts.append('计划 ' + row['planned_date'])
        if row['due_date']:
            parts.append('截止 ' + row['due_date'])
        elif row['due_at']:
            parts.append('截止 ' + str(row['due_at'])[:10])
        return ' · '.join(parts) if parts else '没有计划日，也没有截止'

    def _learning(self, con, now):
        # A node's last movement is its own update or the latest practice/assessment/session
        # on it, whichever is later. A node that was "touched" only by clicking is not moved.
        rows = con.exec_driver_sql(
            "SELECT r.id,r.title,r.updated_at,r.created_at,n.mastery,n.acceptance_criteria, "
            "(SELECT max(x) FROM (SELECT max(s.started_at) AS x FROM learning_sessions s WHERE s.node_id=r.id "
            "UNION ALL SELECT max(a.created_at) FROM learning_assessments a WHERE a.node_id=r.id) WHERE x IS NOT NULL) AS last_activity "
            "FROM resources r JOIN knowledge_nodes n ON n.resource_id=r.id "
            "WHERE r.kind='knowledge_node' AND r.privacy_level='personal' AND r.deleted_at IS NULL "
            "AND n.mastery <> 'verified' AND " + lineage_sql('r')).mappings()
        result = []
        for row in rows:
            candidates = [v for v in (row['updated_at'], row['last_activity']) if v]
            since = max(candidates) if candidates else row['created_at']
            days = _days_since(since, now)
            if days is None:
                continue
            practised = bool(row['last_activity'])
            result.append({
                'id': row['id'], 'kind': 'knowledge_node', 'kind_label': LABELS['knowledge_node'],
                'title': row['title'], 'days': days, 'since': since,
                'basis': '最后练习或验收' if practised else '创建后未练习',
                'detail': '未验收' + (' · 已有验收标准' if row['acceptance_criteria'] else ' · 还没有验收标准'),
                'destination': DESTINATIONS['knowledge_node'],
            })
        return result

    def _projects(self, con, now):
        rows = con.exec_driver_sql(
            "SELECT r.id,r.title,r.updated_at,r.created_at,p.status,p.last_opened_at, "
            "(SELECT max(e.created_at) FROM project_entries e WHERE e.project_id=r.id) AS last_entry "
            "FROM resources r JOIN projects p ON p.resource_id=r.id "
            "WHERE r.kind='project' AND r.privacy_level='personal' AND r.deleted_at IS NULL "
            "AND p.status NOT IN ('archived','completed') AND " + lineage_sql('r')).mappings()
        result = []
        for row in rows:
            candidates = [v for v in (row['updated_at'], row['last_opened_at'], row['last_entry']) if v]
            since = max(candidates) if candidates else row['created_at']
            days = _days_since(since, now)
            if days is None:
                continue
            basis = '最后日志' if row['last_entry'] == since else '最后打开' if row['last_opened_at'] == since else '最后修改'
            result.append({
                'id': row['id'], 'kind': 'project', 'kind_label': LABELS['project'], 'title': row['title'],
                'days': days, 'since': since, 'basis': basis,
                'detail': '进行中' if row['status'] == 'active' else '状态：' + str(row['status']),
                'destination': DESTINATIONS['project'],
            })
        return result
