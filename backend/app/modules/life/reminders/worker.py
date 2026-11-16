"""Single local poller, lock ownership and graceful transaction completion."""
import asyncio
import fcntl
import logging
import os
from .service import ReminderService


class ReminderWorker:
    def __init__(self,settings,interval=5):
        self.settings,self.interval=settings,interval
        self.state='disabled' if not settings.reminders_enabled else 'starting'
        self.stop_event=asyncio.Event();self.task=None;self.descriptor=None

    async def start(self):
        if not self.settings.reminders_enabled:return
        path=self.settings.data_root/'config'/'reminder-worker.lock'
        try:
            self.descriptor=os.open(path,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
            if os.fstat(self.descriptor).st_mode & 0o077:raise PermissionError('private lock required')
            fcntl.flock(self.descriptor,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            self.state='standby';self._release();return
        except OSError:
            self.state='degraded';self._release();return
        self.task=asyncio.create_task(self.run())

    async def run(self):
        while not self.stop_event.is_set():
            try:
                await asyncio.to_thread(ReminderService(self.settings.database).drain)
                self.state='running'
            except Exception as error:
                self.state='degraded'
                logging.getLogger('personal_hub').warning('reminder_tick_failed type=%s',type(error).__name__)
            try:await asyncio.wait_for(self.stop_event.wait(),timeout=self.interval)
            except TimeoutError:pass

    def _release(self):
        if self.descriptor is not None:os.close(self.descriptor);self.descriptor=None

    async def stop(self):
        self.stop_event.set()
        try:
            if self.task:
                try:await asyncio.shield(self.task)
                except asyncio.CancelledError:
                    await self.task
                    raise
        finally:self._release()
