import asyncio,os,fcntl
from .search import SearchService
class SearchWorker:
 def __init__(self,settings):self.settings=settings;self.stop_event=asyncio.Event();self.task=None;self.fd=None;self.state='starting'
 async def start(self):
  try:
   self.fd=os.open(self.settings.data_root/'config'/'search-worker.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
   if os.fstat(self.fd).st_mode&0o077:raise OSError('private lock required')
   fcntl.flock(self.fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
  except (BlockingIOError,OSError):
   if self.fd is not None:os.close(self.fd);self.fd=None
   self.state='standby';return
  self.stop_event.clear();self.task=asyncio.create_task(self.loop())
 async def stop(self):
  self.stop_event.set()
  if self.task:await self.task;self.task=None
  if self.fd is not None:os.close(self.fd);self.fd=None
 async def loop(self):
  while not self.stop_event.is_set():
   try:await asyncio.to_thread(SearchService(self.settings.database).refresh);self.state='running'
   except Exception:self.state='degraded'
   try:await asyncio.wait_for(self.stop_event.wait(),timeout=2)
   except TimeoutError:pass
