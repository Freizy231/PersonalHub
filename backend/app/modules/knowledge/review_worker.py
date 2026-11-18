"""A scheduled review, only when the person asked for one.

This is deliberately not an agent loop. It wakes up, asks whether a review period has just
started and has not already been claimed, and if so computes a review from recorded facts. It
never calls the model on a timer: sending content to a service needs an action the person can
see, so a scheduled review produces a local report and leaves any model step to them.

Two failure modes are designed out:
* Waking late (a closed laptop) must not produce a burst. The slot is a day or a week, and a
  slot that already ran is recorded in `ai_review_slots`.
* A second process must not run twice. The usual private flock is taken like every other
  worker; the loser stays in standby.
"""
import asyncio
import fcntl
import os

from .workspace import WorkspaceService


class ReviewWorker:
    def __init__(self, settings):
        self.settings = settings
        self.stop_event = asyncio.Event()
        self.task = None
        self.fd = None
        self.state = 'disabled'

    async def start(self):
        try:
            self.fd = os.open(self.settings.data_root / 'config' / 'review-worker.lock',
                              os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            if os.fstat(self.fd).st_mode & 0o077:
                raise OSError('private lock required')
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            if self.fd is not None:
                os.close(self.fd); self.fd = None
            self.state = 'standby'; return
        self.state = 'running'
        self.stop_event.clear()
        self.task = asyncio.create_task(self.loop())

    async def stop(self):
        self.stop_event.set()
        if self.task:
            await self.task; self.task = None
        if self.fd is not None:
            os.close(self.fd); self.fd = None
        self.state = 'stopped'

    async def loop(self):
        while not self.stop_event.is_set():
            try:
                await asyncio.to_thread(self.tick)
            except Exception:
                # A failing tick must not kill the worker; the next one retries.
                pass
            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=300)
            except asyncio.TimeoutError:
                pass

    def tick(self):
        """Claim the current slot and record a review for every granted scope.

        Returns what it did, which is also what the tests and the status route read.
        """
        workspace = WorkspaceService(self.settings)
        slot = workspace.review_slot()
        if slot is None:
            return {'slot': None, 'scopes': [], 'reason': 'reviews off'}
        scopes = [g['scope_id'] for g in workspace.grants()
                  if g['kind'] == 'local_write' and g['active'] and g['scope_id']]
        if not scopes:
            return {'slot': slot, 'scopes': [], 'reason': 'no active grant'}
        if workspace.claim_review(slot, None) is None:
            return {'slot': slot, 'scopes': [], 'reason': 'already reviewed'}
        reports = []
        for scope in scopes:
            try:
                reports.append({'scope_id': scope, **workspace.review(scope, None)})
            except Exception:
                continue
        return {'slot': slot, 'scopes': scopes, 'reports': reports}
