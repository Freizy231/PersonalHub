"""Acting on something that has gone quiet.

The staleness report says what stopped moving. This turns "I looked at it" into a decision
with a consequence, so the report drains instead of becoming a wall of guilt.

Three rules shape the whole file:

* **A review never rewrites history.** The row is a new fact with its own date. `updated_at`
  is bumped only when the resource itself really changed (a task moved, a project's status
  or target date changed). A decision to defer must not make a stale thing look fresh,
  otherwise the view could no longer tell "moving" from "triaged".
* **The consequence lives where the consequence already lives.** Finishing a task sets its
  status, deferring a project sets its target date — the same fields the rest of the app
  reads. No parallel scheduling model is invented here.
* **What cannot be honestly recorded is refused.** A knowledge node becomes "learned" only
  through an assessment that keeps its criteria snapshot and evidence; a review button must
  not be able to fake that.

A review with a future `next_date` hides the item until that date: that is what both
"继续" and "以后再说" mean.
"""
from datetime import date, datetime, timedelta
from uuid import uuid4

from ...core.database import HubError, connection, utc_now
from ...core.operations import activity, record, replay
from .service import lineage_sql, rows, visible
from .stalled import LABELS, THRESHOLDS

# Decisions are shared by every kind; what each one does differs by kind.
DECISIONS = ('done', 'continue', 'snooze', 'drop')

# Kinds whose "done" can be recorded without pretending an unverified learning state.
CAN_FINISH = {'task', 'project'}


def _local_today():
    # Review dates are what the person sees on their calendar, not UTC's.
    return datetime.now().astimezone().date()


def _parse(day):
    if not day:
        return None
    return date.fromisoformat(str(day)[:10])


def _default_next(kind, from_day):
    # "Ask me again" reuses the kind's own rhythm, so a deferral is as long as the thing's
    # normal quiet period rather than an arbitrary constant.
    return from_day + timedelta(days=THRESHOLDS.get(kind, 14))


class ReviewService:
    def __init__(self, database):
        self.database = database

    # ------------------------------------------------------------------ read

    def latest(self, con, resource_ids):
        """The most recent review per resource, for annotating the stalled report."""
        if not resource_ids:
            return {}
        marks = ','.join('?' for _ in resource_ids)
        found = rows(con, 'SELECT id,resource_id,decision,review_date,note,next_date,created_at '
                          'FROM stalled_reviews WHERE resource_id IN (' + marks + ') '
                          'ORDER BY created_at DESC', tuple(resource_ids))
        latest = {}
        for row in found:
            latest.setdefault(row['resource_id'], row)  # first seen is the newest
        return latest

    def recent(self, limit=50):
        with connection(self.database) as con:
            # A "drop" decision moves the resource to the recycle bin; the record of having
            # decided must survive that, so this history does not filter on deleted_at.
            items = rows(con, 'SELECT v.id,v.resource_id,v.decision,v.review_date,v.note,v.next_date,v.created_at,'
                              'r.title,r.kind,r.deleted_at FROM stalled_reviews v JOIN resources r ON r.id=v.resource_id '
                              "WHERE r.privacy_level='personal' AND " + lineage_sql('r', trash=True) +
                              ' ORDER BY v.created_at DESC LIMIT ?', (limit,))
            for item in items:
                item['kind_label'] = LABELS.get(item['kind'], item['kind'])
                item['in_trash'] = bool(item.pop('deleted_at'))
            return {'items': items, 'total': len(items)}

    # ----------------------------------------------------------------- write

    def record_decision(self, resource_id, payload, key):
        data = payload.model_dump(mode='json')
        operation = 'knowledge.review:' + resource_id
        with connection(self.database, write=True) as con:
            current = visible(con, resource_id)
            if current['version'] != payload.expected_version:
                raise HubError('VERSION_CONFLICT')
            prior = replay(con, operation, key, data)
            if prior:
                return {'review': prior['review'], 'resource': visible(con, resource_id, trash=True)}

            kind = current['kind']
            if kind not in THRESHOLDS:
                raise HubError('REVIEW_UNSUPPORTED_KIND', 422)
            decision = payload.decision
            if decision == 'done' and kind not in CAN_FINISH:
                raise HubError('REVIEW_DONE_NEEDS_ASSESSMENT', 422)

            today = _local_today()
            next_day = _parse(data.get('next_date'))
            if decision in ('continue', 'snooze') and next_day is None:
                next_day = _default_next(kind, today)
            if next_day is not None and next_day <= today:
                raise HubError('REVIEW_DATE_NOT_FUTURE', 422)
            if decision in ('done', 'drop') and next_day is not None:
                raise HubError('REVIEW_DATE_NOT_ALLOWED', 422)

            self._apply(con, current, decision, next_day, today)

            review = {
                'id': str(uuid4()), 'resource_id': resource_id, 'decision': decision,
                'review_date': today.isoformat(), 'note': payload.note,
                'next_date': next_day.isoformat() if next_day else None,
                'created_at': utc_now(),
            }
            con.exec_driver_sql(
                'INSERT INTO stalled_reviews(id,resource_id,decision,review_date,note,next_date,created_at) '
                'VALUES(?,?,?,?,?,?,?)',
                (review['id'], resource_id, decision, review['review_date'], review['note'],
                 review['next_date'], review['created_at']))
            activity(con, resource_id, 'reviewed', ['decision'])
            record(con, operation, key, data, {'review': review})
            # "drop" moves the resource to the recycle bin, so read the result as trash.
            return {'review': review, 'resource': visible(con, resource_id, trash=True)}

    # --------------------------------------------------------------- effects

    def _apply(self, con, current, decision, next_day, today):
        """Carry the decision into the fields the rest of the app already reads."""
        kind, rid = current['kind'], current['id']
        if kind == 'task':
            self._task(con, rid, decision, next_day, today)
        elif kind == 'project':
            self._project(con, rid, decision, next_day)
        elif kind == 'knowledge_node':
            self._node(con, rid, decision)
        # 'snooze' is deliberately absent from every branch: it records the decision and
        # changes nothing about the resource, which is exactly the point of deferring.

    def _task(self, con, rid, decision, next_day, today):
        from ..life.reminders.service import cancel_pending

        if decision == 'snooze':
            return
        status = con.exec_driver_sql('SELECT status FROM tasks WHERE resource_id=?', (rid,)).scalar_one()
        if decision == 'done':
            if status == 'done':
                return
            con.exec_driver_sql("UPDATE tasks SET status='done',completed_at=? WHERE resource_id=?",
                                (utc_now(), rid))
            cancel_pending(con, rid)
        elif decision == 'drop':
            if status == 'cancelled':
                return
            con.exec_driver_sql("UPDATE tasks SET status='cancelled',completed_at=NULL WHERE resource_id=?",
                                (rid,))
            cancel_pending(con, rid)
        elif decision == 'continue':
            # Moving the task onto a future day is the reschedule; it is a real change, so
            # updated_at moves with it and the staleness clock restarts honestly.
            if status in ('done', 'cancelled'):
                raise HubError('REVIEW_ALREADY_FINISHED', 409)
            con.exec_driver_sql('UPDATE tasks SET planned_date=? WHERE resource_id=?',
                                (next_day.isoformat(), rid))
        self._touch(con, rid)

    def _project(self, con, rid, decision, next_day):
        if decision == 'snooze':
            return
        status = con.exec_driver_sql('SELECT status FROM projects WHERE resource_id=?', (rid,)).scalar_one()
        if decision == 'done':
            if status == 'completed':
                return
            con.exec_driver_sql("UPDATE projects SET status='completed' WHERE resource_id=?", (rid,))
        elif decision == 'drop':
            if status == 'archived':
                return
            con.exec_driver_sql("UPDATE projects SET status='archived' WHERE resource_id=?", (rid,))
        elif decision == 'continue':
            con.exec_driver_sql('UPDATE projects SET status=?,target_date=? WHERE resource_id=?',
                                ('active' if status in ('planned', 'paused') else status,
                                 next_day.isoformat(), rid))
        self._touch(con, rid)

    def _node(self, con, rid, decision):
        if decision == 'snooze':
            return
        if decision == 'drop':
            # Setting a node aside means removing it from the tree; the recycle bin keeps it
            # recoverable, and an assessment-based "done" stays available in the learning page.
            active_child = con.exec_driver_sql(
                'SELECT 1 FROM knowledge_nodes x JOIN resources r ON r.id=x.resource_id '
                'WHERE (x.path_id=? OR x.parent_id=?) AND r.deleted_at IS NULL LIMIT 1', (rid, rid)).first()
            if active_child:
                raise HubError('LEARNING_HAS_ACTIVE_CHILDREN', 409)
            con.exec_driver_sql('UPDATE resources SET deleted_at=? WHERE id=?', (utc_now(), rid))
            self._touch(con, rid)
            return
        if decision == 'done':
            raise HubError('REVIEW_DONE_NEEDS_ASSESSMENT', 422)
        # 'continue' only dates the next look; a node moves by practising, not by agreeing to.

    @staticmethod
    def _touch(con, rid):
        con.exec_driver_sql('UPDATE resources SET version=version+1,updated_at=? WHERE id=?',
                            (utc_now(), rid))
