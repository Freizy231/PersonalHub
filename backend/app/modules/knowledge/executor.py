"""Executing registered capabilities under authorization, with a durable record.

The executor is the only place that turns a plan or template into side effects. Its job is
narrow on purpose:

1. Re-check the grant at every write boundary (a revocation mid-run stops the next step).
2. Give each step a stable idempotency key derived from the run and its position, so a retry
   or a crash-recovery replay cannot create a second goal, task or note.
3. Record what actually happened, step by step, so a partial run reports honestly instead of
   claiming success.

It never interprets model output. A step must already name a registered capability id and
carry arguments that pass the contract; the model's job ended before this code runs.
"""
import json
from uuid import uuid4

from ...core.capabilities import CapabilityError, CapabilityResult, check_arguments
from ...core.database import HubError, connection, utc_now
from .adapters import Adapters, SPECS


class Executor:
    def __init__(self, settings):
        self.settings = settings
        self.database = settings.database
        self.adapters = Adapters(settings)

    # ------------------------------------------------------------------ registry

    def specs(self):
        return {sid: {
            'id': spec.id, 'version': spec.version, 'title': spec.title, 'purpose': spec.purpose,
            'effect': spec.effect, 'fields': list(spec.fields), 'reversible': spec.reversible,
        } for sid, spec in SPECS.items()}

    def resolve(self, capability_id, version=None):
        spec = SPECS.get(capability_id)
        if spec is None:
            # An unknown id is a refusal, never a fallback to something "close enough".
            raise CapabilityError('CAPABILITY_UNKNOWN', 422)
        if version is not None and version != spec.version:
            raise CapabilityError('CAPABILITY_VERSION_MISMATCH', 409)
        return spec

    # --------------------------------------------------------------------- runs

    def create_run(self, owner, steps, trigger='manual', plan_id=None, template_id=None, grant_id=None):
        """Persist a run and its steps before executing anything.

        Writing first means a process that dies mid-run leaves a record saying what it was
        doing, rather than vanishing.
        """
        run_id = str(uuid4())
        operation_key = str(uuid4())
        now = utc_now()
        with connection(self.database, write=True) as con:
            con.exec_driver_sql(
                'INSERT INTO ai_runs(id,operation_key,owner_hash,trigger,plan_id,template_id,grant_id,status,'
                'error_code,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                (run_id, operation_key, owner, trigger, plan_id, template_id, grant_id, 'queued', None, now, now))
            for position, step in enumerate(steps):
                con.exec_driver_sql(
                    'INSERT INTO ai_run_steps(id,run_id,position,capability_id,capability_version,'
                    'arguments_json,idempotency_key,status,result_summary,resource_ids_json,error_code,'
                    'started_at,finished_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (str(uuid4()), run_id, position, step['capability_id'], step['capability_version'],
                     json.dumps(step['arguments'], ensure_ascii=False), f'{run_id}:{position}',
                     'pending', '', '[]', None, None, None))
        return run_id

    def execute(self, run_id, scope_id, owner):
        """Run the steps in order, stopping at the first refusal.

        An authorization check runs before each step, not once at the start: the person can
        revoke while a run is in flight, and the requirement is that later steps stop.
        """
        steps = self._steps(run_id)
        self._set_run(run_id, 'running')
        finished, total = 0, len(steps)
        # Real ids of things this run created, by kind and by slot index. A plan written before
        # anything existed refers to "$created_node 0"; here that becomes a real id.
        made = {'path': [], 'node': []}
        for step in steps:
            if self._cancelled(run_id):
                break
            spec = SPECS.get(step['capability_id'])
            if spec is None:
                self._finish_step(step, 'failed', 'CAPABILITY_UNKNOWN')
                self._set_run(run_id, 'partial' if finished else 'failed', 'CAPABILITY_UNKNOWN')
                return self.status(run_id, owner)
            if spec.effect in ('create', 'update') and not self.granted(scope_id):
                self._finish_step(step, 'failed', 'CAPABILITY_SCOPE_DENIED')
                self._set_run(run_id, 'partial' if finished else 'failed', 'CAPABILITY_SCOPE_DENIED')
                return self.status(run_id, owner)
            self._start_step(step)
            try:
                arguments = json.loads(step['arguments_json'])
                arguments = self._bind(arguments, made)
                check_arguments(spec, arguments)
                if spec.id not in self.adapters.calls:
                    raise CapabilityError('CAPABILITY_UNKNOWN', 422)
                result = self.adapters.calls[spec.id](arguments, scope_id, self._key(step))
                if not isinstance(result, CapabilityResult):
                    raise CapabilityError('CAPABILITY_RESULT_INVALID', 500)
                self._record_created(step['capability_id'], result, made)
                self._finish_step(step, 'done', None, result)
                finished += 1
            except CapabilityError as error:
                self._finish_step(step, 'failed', error.code)
                self._set_run(run_id, 'partial' if finished else 'failed', error.code)
                return self.status(run_id, owner)
            except HubError as error:
                self._finish_step(step, 'failed', error.code)
                self._set_run(run_id, 'partial' if finished else 'failed', error.code)
                return self.status(run_id, owner)
            except Exception:
                # Never claim a step succeeded because an unexpected error escaped. The
                # partial run is recorded first, then the failure is raised so the caller
                # receives the same typed error every other route returns.
                self._finish_step(step, 'failed', 'AI_CAPABILITY_EXECUTION_FAILED')
                self._set_run(run_id, 'partial' if finished else 'failed', 'AI_CAPABILITY_EXECUTION_FAILED')
                raise HubError('AI_CAPABILITY_EXECUTION_FAILED', 503) from None
        status = 'success' if finished == total else ('partial' if finished else 'cancelled')
        self._set_run(run_id, status)
        return self.status(run_id, owner)

    # ------------------------------------------------------------------- status

    def status(self, run_id, owner):
        with connection(self.database) as con:
            run = con.exec_driver_sql('SELECT * FROM ai_runs WHERE id=? AND owner_hash=?',
                                      (run_id, owner)).mappings().first()
            if not run:
                raise HubError('LOCAL_RECORD_NOT_FOUND', 404)
            steps = con.exec_driver_sql(
                'SELECT position,capability_id,status,result_summary,resource_ids_json,error_code '
                'FROM ai_run_steps WHERE run_id=? ORDER BY position', (run_id,)).mappings().all()
        return {
            'id': run['id'], 'status': run['status'], 'error_code': run['error_code'],
            'created_at': run['created_at'], 'updated_at': run['updated_at'],
            'steps': [{'position': s['position'], 'capability_id': s['capability_id'], 'status': s['status'],
                       'summary': s['result_summary'], 'resource_ids': json.loads(s['resource_ids_json']),
                       'error_code': s['error_code']} for s in steps],
        }

    def cancel(self, run_id, owner):
        self.status(run_id, owner)
        with connection(self.database, write=True) as con:
            con.exec_driver_sql(
                "UPDATE ai_runs SET status='cancelled',updated_at=? WHERE id=? AND status IN ('queued','running')",
                (utc_now(), run_id))
            con.exec_driver_sql(
                "UPDATE ai_run_steps SET status='skipped' WHERE run_id=? AND status='pending'", (run_id,))
        return self.status(run_id, owner)

    # ------------------------------------------------------------------ grants

    def granted(self, scope_id):
        """Whether local writes in this scope are allowed right now.

        A restored backup or a disabled grant returns False, which is the safe default.
        """
        if not scope_id:
            return False
        with connection(self.database) as con:
            row = con.exec_driver_sql(
                "SELECT enabled,expires_at FROM ai_grants WHERE kind='local_write' AND scope_id=?",
                (scope_id,)).mappings().first()
        if not row or not row['enabled']:
            return False
        if row['expires_at'] and row['expires_at'] <= utc_now():
            return False
        return True

    # ---------------------------------------------------------------- internals

    def _steps(self, run_id):
        with connection(self.database) as con:
            return [dict(r) for r in con.exec_driver_sql(
                'SELECT * FROM ai_run_steps WHERE run_id=? ORDER BY position', (run_id,)).mappings()]

    @staticmethod
    def _bind(arguments, made):
        """Replace a plan's symbolic slot with the id the run actually created."""
        bound = dict(arguments)
        for field in ('path_id', 'goal_id', 'node_id'):
            value = bound.get(field)
            if not isinstance(value, dict):
                continue
            for slot_key, bucket in (('$path_slot', 'path'), ('$node_slot', 'node')):
                if slot_key not in value:
                    continue
                index = int(value[slot_key])
                if index < 0 or index >= len(made[bucket]):
                    raise CapabilityError('AI_PLAN_REFERENCE_UNRESOLVED', 409)
                bound[field] = made[bucket][index]
        return bound

    @staticmethod
    def _record_created(capability_id, result, made):
        if capability_id == 'learning.path.create' and result.resource_ids:
            made['path'].append(result.resource_ids[0])
        elif capability_id == 'learning.node.create' and result.resource_ids:
            made['node'].append(result.resource_ids[0])

    def _key(self, step):
        # Stable across retries of the same step, distinct across steps.
        return step['idempotency_key']

    def _cancelled(self, run_id):
        with connection(self.database) as con:
            status = con.exec_driver_sql('SELECT status FROM ai_runs WHERE id=?', (run_id,)).scalar_one()
        return status == 'cancelled'

    def _set_run(self, run_id, status, error=None):
        with connection(self.database, write=True) as con:
            con.exec_driver_sql('UPDATE ai_runs SET status=?,error_code=?,updated_at=? WHERE id=?',
                                (status, error, utc_now(), run_id))

    def _start_step(self, step):
        with connection(self.database, write=True) as con:
            con.exec_driver_sql("UPDATE ai_run_steps SET status='running',started_at=? WHERE id=?",
                                (utc_now(), step['id']))

    def _finish_step(self, step, status, error=None, result=None):
        with connection(self.database, write=True) as con:
            con.exec_driver_sql(
                'UPDATE ai_run_steps SET status=?,result_summary=?,resource_ids_json=?,error_code=?,finished_at=? '
                'WHERE id=?',
                (status, (result.summary if result else '')[:4000],
                 json.dumps(list(result.resource_ids) if result else []), error, utc_now(), step['id']))
