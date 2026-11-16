"""Time-based collection, only when the person has turned it on.

The schedule lives in `intel_schedule` and starts disabled. Nothing here touches the
network until `enabled` is 1, and each source records the slot it last ran for, so a
restart, a clock change or a laptop waking up cannot cause a burst of collection.
"""
import asyncio
import fcntl
import os
from datetime import datetime

from .service import IntelligenceService


class ScheduleWorker:
    def __init__(self, settings):
        self.settings = settings
        self.stop_event = asyncio.Event()
        self.task = None
        self.fd = None
        self.state = 'disabled'

    async def start(self):
        try:
            self.fd = os.open(self.settings.data_root / 'config' / 'intel-worker.lock',
                              os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            if os.fstat(self.fd).st_mode & 0o077:
                raise OSError('private lock required')
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            if self.fd is not None:
                os.close(self.fd); self.fd = None
            self.state = 'standby'; return
        self.stop_event.clear()
        self.task = asyncio.create_task(self.loop())

    async def stop(self):
        self.stop_event.set()
        if self.task:
            await self.task; self.task = None
        if self.fd is not None:
            os.close(self.fd); self.fd = None

    async def tick(self):
        """Run at most one slot's worth of collection. Returns what it did."""
        service = IntelligenceService(self.settings)
        schedule = await asyncio.to_thread(service.schedule)
        if not schedule['enabled']:
            self.state = 'disabled'
            return []
        self.state = 'running'
        due = await asyncio.to_thread(service.due_sources, datetime.now().astimezone())
        done = []
        for source in due:
            if self.stop_event.is_set():
                break
            # Mark first: a crash mid-collect must not cause the same slot to run again.
            await asyncio.to_thread(service.mark_scheduled, source['id'], source['slot'])
            try:
                async with asyncio.timeout(130):
                    await service.collect(source['id'], str(__import__('uuid').uuid4()))
            except (asyncio.CancelledError, TimeoutError):
                raise
            except Exception:
                # One failing source must not stop the others; the run row records the failure.
                pass
            done.append(source['id'])
        return done

    async def loop(self):
        while not self.stop_event.is_set():
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                self.state = 'degraded'
            try:
                await asyncio.wait_for(self.stop_event.wait(), timeout=60)
            except TimeoutError:
                pass
