"""Isolated deterministic Phase 2 scale probe. No production files or network sockets."""
import argparse,asyncio,hashlib,json,math,os,platform,statistics,tempfile,time
from datetime import date,timedelta
from pathlib import Path
from uuid import uuid4
import httpx
from backend.manage import initialize_empty
from backend.maintenance import upgrade
from backend.app.core.config import Settings
from backend.app.core.database import connection,utc_now
from backend.app.main import create_app
from backend.app.modules.library.documents.storage import BlobFiles
from backend.app.modules.knowledge.search import SearchService
from backend.tests.async_support import PollingLoop
from backend.tests.pdf_fixtures import pdf
from scripts.data_safety import write_json

def p95(samples):return round(sorted(samples)[math.ceil(.95*len(samples))-1]*1000,2)
def seed(settings):
 store=BlobFiles(settings.data_root);now=utc_now();today=date.today().isoformat()
 with connection(settings.database,write=True) as c:
  for i in range(10000):
   rid=str(uuid4());c.exec_driver_sql("INSERT INTO resources(id,kind,title,privacy_level,created_at,updated_at,version) VALUES(?,'task',?,'personal',?,?,1)",(rid,f'PE 导入表练习 {i}',now,now))
   c.exec_driver_sql("INSERT INTO tasks(resource_id,description,status,priority,planned_date,timezone,position) VALUES(?,?,'todo',2,?,'Asia/Shanghai',0)",(rid,'PE 实验 分析导入表与逆向',today))
  for i in range(1000):
   rid=str(uuid4());bid=str(uuid4());data=pdf(text=f'Personal Hub PE fixture {i}');digest=hashlib.sha256(data).hexdigest();relative='library/blobs/'+digest[:2]+'/'+digest+'.pdf'
   fd=store.create(relative)
   with os.fdopen(fd,'wb') as f:f.write(data)
   extracted=f'Personal Hub PE fixture {i}\n恶意代码分析 导入表'.encode();text_path='library/text/'+rid+'/'+str(uuid4())+'.txt';text_hash=store.write_text(text_path,extracted)
   c.exec_driver_sql("INSERT INTO resources(id,kind,title,privacy_level,created_at,updated_at,version) VALUES(?,'document',?,'personal',?,?,1)",(rid,f'PDF fixture {i}',now,now))
   c.exec_driver_sql('INSERT INTO blobs VALUES(?,?,?,?,?,?)',(bid,digest,len(data),relative,'application/pdf',now))
   c.exec_driver_sql("INSERT INTO documents(resource_id,blob_id,original_name,page_count,extraction_status,extracted_text_path,text_sha256,extractor) VALUES(?,?,?,1,'ready',?,?,'synthetic-scale-fixture')",(rid,bid,f'fixture-{i}.pdf',text_path,text_hash))
 index_start=time.perf_counter();indexed=SearchService(settings.database).rebuild()
 return {'tasks':10000,'pdfs':1000,'resources':11000,'index_seconds':round(time.perf_counter()-index_start,3),'indexed':indexed['indexed'],'pdf_text_origin':'synthetic fixture; parser correctness tested separately on real Poppler'}
async def benchmark(settings):
 app=create_app(settings);origin='http://127.0.0.1:8765'
 async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=origin) as client:
  result=await client.post('/api/v1/session/exchange',json={'token':app.state.auth.bootstrap},headers={'Origin':origin})
  headers={'Origin':origin,'X-CSRF-Token':result.json()['csrf_token']}
  today=date.today();tomorrow=today+timedelta(days=1)
  homepage=['/dashboard/preferences',f'/tasks?scope=today&on_date={today}&limit=8',f'/events?start_date={today}&end_date={tomorrow}', '/reminders?limit=20','/documents?state=reading&limit=6','/learning/overview']
  async def home():
   for r in await asyncio.gather(*[client.get('/api/v1'+p) for p in homepage]):
    if r.status_code!=200:raise RuntimeError('homepage endpoint failed: '+str(r.status_code))
  async def write():
   r=await client.post('/api/v1/tasks',json={'title':'性能测试虚构任务'},headers={**headers,'Idempotency-Key':str(uuid4())})
   if r.status_code!=201:raise RuntimeError('task write failed')
  async def search():
   r=await client.get('/api/v1/search',params={'q':'恶意代码','limit':50})
   if r.status_code!=200 or r.json()['total']!=1000:raise RuntimeError('fulltext failed')
  async def short():
   r=await client.get('/api/v1/search',params={'q':'PE','limit':50})
   if r.status_code!=200 or len(r.json()['items'])!=50:raise RuntimeError('short-word search failed')
  results={}
  for name,fn,target in [('homepage_api_bundle',home,2000),('task_write_api',write,500),('fts_pdf_search_api',search,1000),('short_word_search_api',short,1000)]:
   for _ in range(20):await fn()
   samples=[]
   for _ in range(100):start=time.perf_counter();await fn();samples.append(time.perf_counter()-start)
   ms=p95(samples);results[name]={'warmups':20,'samples':100,'p95_ms':ms,'median_ms':round(statistics.median(samples)*1000,2),'target_ms':target,'met':ms<=target}
  return results

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
 with tempfile.TemporaryDirectory(prefix='hub-phase2-scale-') as temp:
  settings=Settings(data_root=Path(temp)/'hub',legacy_database=Path(temp)/'unused-legacy',reminders_enabled=False)
  initialize_empty(settings.data_root);upgrade(settings);scale=seed(settings)
  with asyncio.Runner(loop_factory=PollingLoop) as runner:results=runner.run(benchmark(settings))
  report={'format_version':1,'created_at':utc_now(),'environment':{'python':platform.python_version(),'cpus':os.cpu_count(),'memory_kib':int(Path('/proc/meminfo').read_text().splitlines()[0].split()[1])},'scale':scale,'results':results,'browser_measured':False,'scope':'in-process ASGI + real database/files, no TCP/browser, extraction not concurrent'}
  write_json(args.output,report);print(json.dumps({'scale':scale,'results':results,'browser_measured':False},ensure_ascii=False))
if __name__=='__main__':main()
