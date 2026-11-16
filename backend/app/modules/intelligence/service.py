"""Explicit collectors; network outside transactions, immutable raw snapshots."""
import asyncio,hashlib,json,sqlite3
from contextlib import closing
from datetime import datetime,timedelta,timezone
from uuid import uuid4,uuid5,NAMESPACE_URL
from ...core.database import connection,HubError,utc_now
from ...core.operations import replay,record,request_hash
from ...core.outbound import public_url
from ..knowledge.service import rows
from .collectors import COLLECTORS,DEFAULTS,target

NS=uuid5(NAMESPACE_URL,'personal-hub/intelligence/v1')
def stable(source,rid):return str(uuid5(NS,source+'/'+str(rid)))
def compact(v):return json.dumps(v,ensure_ascii=False,separators=(',',':'),allow_nan=False)

class IntelligenceService:
    def __init__(self,settings):self.settings=settings;self.database=settings.database
    def sources(self):
        with connection(self.database) as c:return {'items':rows(c,"SELECT s.*,COALESCE(t.query,'') AS query,t.last_scheduled_at FROM intel_sources s LEFT JOIN intel_source_settings t ON t.source_id=s.id ORDER BY s.created_at,s.id"),
            'unconnected':[{'kind':k,'status':'not_connected','reason':'需独立接口权限与维护评估'} for k in ['reddit','lobsters','product_hunt']],
            'github':'legacy_readonly_import; original collector remains sole legacy writer'}
    def source_save(self,p,key,rid=None):
        endpoint=public_url(p.endpoint if p.kind in ('rss','arxiv') and p.endpoint else DEFAULTS.get(p.kind,''))
        with connection(self.database,write=True) as c:
            body={'id':rid,'body':p.model_dump(mode='json')};old=replay(c,'intel.source',key,body)
            if old:return rows(c,"SELECT s.*,COALESCE(t.query,'') AS query,t.last_scheduled_at FROM intel_sources s LEFT JOIN intel_source_settings t ON t.source_id=s.id WHERE s.id=?",(old['id'],))[0]
            now=utc_now()
            if rid:
                current=rows(c,'SELECT * FROM intel_sources WHERE id=?',(rid,))
                if not current:raise HubError('LOCAL_RECORD_NOT_FOUND',404)
                if current[0]['version']!=p.expected_version:raise HubError('VERSION_CONFLICT')
                if current[0]['kind']!=p.kind:raise HubError('SOURCE_KIND_IMMUTABLE')
                if c.exec_driver_sql("SELECT 1 FROM intel_runs WHERE source_id=? AND status='running'",(rid,)).first():raise HubError('COLLECTOR_BUSY')
                c.exec_driver_sql('UPDATE intel_sources SET name=?,endpoint=?,enabled=?,version=version+1,updated_at=? WHERE id=?',(p.name,endpoint,int(p.enabled),now,rid))
            else:
                if p.expected_version:raise HubError('VERSION_CONFLICT')
                if c.exec_driver_sql('SELECT count(*) FROM intel_sources').scalar()>=50:raise HubError('LOCAL_CAPACITY')
                rid=str(uuid4());c.exec_driver_sql('INSERT INTO intel_sources(id,kind,name,endpoint,enabled,version,created_at,updated_at) VALUES(?,?,?,?,?,1,?,?)',(rid,p.kind,p.name,endpoint,int(p.enabled),now,now))
            # The query and the last scheduled slot are separate facts in their own table.
            c.exec_driver_sql('INSERT INTO intel_source_settings(source_id,query,last_scheduled_at,updated_at) VALUES(?,?,NULL,?) '
                              'ON CONFLICT(source_id) DO UPDATE SET query=excluded.query,updated_at=excluded.updated_at',(rid,p.query,now))
            record(c,'intel.source',key,body,{'id':rid})
            return rows(c,"SELECT s.*,COALESCE(t.query,'') AS query,t.last_scheduled_at FROM intel_sources s LEFT JOIN intel_source_settings t ON t.source_id=s.id WHERE s.id=?",(rid,))[0]
    def list(self,q='',source='',domain='',state='',favorite=False,subscribed=False,limit=50,offset=0):
        with connection(self.database) as c:
            clause="(?='' OR instr(lower(i.title||' '||i.summary),lower(?))>0) AND (?='' OR i.source_key=?) AND (?='' OR i.domain=?) AND (?='' OR i.read_state=?) AND (?=0 OR i.favorite=1) AND (?=0 OR EXISTS(SELECT 1 FROM intel_subscriptions s WHERE s.enabled=1 AND instr(lower(i.title||' '||i.summary),lower(s.keyword))>0))"
            args=(q,q,source,source,domain,domain,state,state,int(favorite),int(subscribed))
            total=c.exec_driver_sql('SELECT count(*) FROM intel_items i WHERE '+clause,args).scalar()
            items=rows(c,"SELECT i.*,COALESCE(t.summary_zh,'') AS summary_zh FROM intel_items i LEFT JOIN intel_item_text t ON t.item_id=i.id WHERE "+clause+' ORDER BY i.updated_at DESC,i.id LIMIT ? OFFSET ?',args+(limit,offset))
            self._attach_sparklines(c,items)
            return {'items':items,'total':total,'limit':limit,'offset':offset}
    def _attach_sparklines(self,c,items):
        """A tiny series per item so the list can draw its own trend.

        Star/fork for GitHub-imported rows, score for Hacker News, nothing for feeds that
        carry no numeric metric: an absent series stays absent rather than becoming a flat
        line that would read as "no change".
        """
        if not items:return
        ids=[i['id'] for i in items]
        marks=','.join('?' for _ in ids)
        found=rows(c,f'SELECT item_id,captured_at,metrics_json FROM intel_snapshots WHERE item_id IN ({marks}) ORDER BY captured_at',tuple(ids))
        by_item={}
        for row in found:
            try:metrics=json.loads(row['metrics_json'])
            except (ValueError,TypeError):continue
            by_item.setdefault(row['item_id'],[]).append((row['captured_at'],metrics))
        for item in items:
            series=by_item.get(item['id'],[])
            item['trend']={'stars':[],'forks':[],'score':[],'series':0}
            if len(series)<2:continue
            for _,metrics in series[-30:]:
                for key in ('stars','forks','score'):
                    value=metrics.get(key)
                    if isinstance(value,int) and not isinstance(value,bool):item['trend'][key].append(value)
            item['trend']['series']=len(series[-30:])

    def schedule(self):
        with connection(self.database) as c:
            r=rows(c,'SELECT * FROM intel_schedule WHERE id=1')
        if not r:
            return {'enabled':False,'runs_per_day':2,'local_hours':['09:00','21:00'],'last_run_at':None,'next_run_at':None,'version':0}
        row=r[0]
        return {'enabled':bool(row['enabled']),'runs_per_day':row['runs_per_day'],
                'local_hours':[h for h in (row['local_hours'] or '').split(',') if h],
                'last_run_at':row['last_run_at'],'next_run_at':row['next_run_at'],'version':row['version']}

    def schedule_save(self,p,key):
        hours=','.join(p.local_hours)
        with connection(self.database,write=True) as c:
            body={'enabled':p.enabled,'runs_per_day':p.runs_per_day,'hours':hours,'expected':p.expected_version}
            if replay(c,'intel.schedule',key,body):return self.schedule()
            current=rows(c,'SELECT * FROM intel_schedule WHERE id=1')
            version=current[0]['version'] if current else 0
            if version!=p.expected_version:raise HubError('VERSION_CONFLICT')
            now=utc_now()
            if current:
                c.exec_driver_sql('UPDATE intel_schedule SET enabled=?,runs_per_day=?,local_hours=?,version=version+1,updated_at=?,next_run_at=? WHERE id=1',
                                  (int(p.enabled),p.runs_per_day,hours,now,None if not p.enabled else current[0]['next_run_at']))
            else:
                c.exec_driver_sql('INSERT INTO intel_schedule(id,enabled,runs_per_day,local_hours,version,updated_at) VALUES(1,?,?,?,1,?)',
                                  (int(p.enabled),p.runs_per_day,hours,now))
            record(c,'intel.schedule',key,body,{'hours':hours})
        return self.schedule()

    def due_sources(self,now_local):
        """Enabled sources whose slot has passed, and only when the schedule is on."""
        schedule=self.schedule()
        if not schedule['enabled']:return []
        slot=self.current_slot(schedule,now_local)
        if slot is None:return []
        with connection(self.database) as c:
            rows_=rows(c,"SELECT s.*,COALESCE(t.query,'') AS query,t.last_scheduled_at FROM intel_sources s LEFT JOIN intel_source_settings t ON t.source_id=s.id WHERE s.enabled=1 ORDER BY s.created_at,s.id")
        return [{**r,'slot':slot} for r in rows_ if (r['last_scheduled_at'] or '')<slot]

    @staticmethod
    def current_slot(schedule,now_local):
        """The most recent configured start time that has already passed, as an ISO local string."""
        from datetime import datetime
        best=None
        for hour in schedule['local_hours']:
            hh,mm=(int(x) for x in hour.split(':'))
            at=now_local.replace(hour=hh,minute=mm,second=0,microsecond=0)
            if at<=now_local and (best is None or at>best):best=at
        if best is None:
            # No slot yet today: yesterday's last slot still has to run.
            hour=max(schedule['local_hours'])
            hh,mm=(int(x) for x in hour.split(':'))
            at=(now_local.replace(hour=hh,minute=mm,second=0,microsecond=0)-timedelta(days=1))
            return at.isoformat(timespec='seconds')
        return best.isoformat(timespec='seconds')

    def mark_scheduled(self,sid,slot):
        with connection(self.database,write=True) as c:
            c.exec_driver_sql('INSERT INTO intel_source_settings(source_id,query,last_scheduled_at,updated_at) VALUES(?,\'\',?,?) '
                              'ON CONFLICT(source_id) DO UPDATE SET last_scheduled_at=excluded.last_scheduled_at',(sid,slot,utc_now()))

    def detail(self,rid):
        with connection(self.database) as c:
            found=rows(c,"SELECT i.*,COALESCE(t.summary_zh,'') AS summary_zh FROM intel_items i LEFT JOIN intel_item_text t ON t.item_id=i.id WHERE i.id=?",(rid,))
            if not found:raise HubError('LOCAL_RECORD_NOT_FOUND',404)
            snapshots=rows(c,'SELECT * FROM intel_snapshots WHERE item_id=? ORDER BY captured_at DESC,id DESC LIMIT 100',(rid,))
            trend={'quality':'insufficient_history','metrics':{},'note':'仅同来源同名累计指标差值；非统一热度分数。'}
            # TrendingScore/download rolling windows must not be treated as cumulative growth.
            if len(snapshots)>=2:
                latest,base=snapshots[0],None
                try:
                    end=datetime.fromisoformat(latest['captured_at'].replace('Z','+00:00'))
                    for s in snapshots[1:]:
                        if datetime.fromisoformat(s['captured_at'].replace('Z','+00:00'))<=end-timedelta(days=7):base=s;break
                    if base:
                        a=json.loads(latest['metrics_json']);b=json.loads(base['metrics_json'])
                        trend['metrics']={k:{'latest':a[k],'baseline':b[k],'delta':a[k]-b[k],'relative':None if b[k]==0 else (a[k]-b[k])/b[k]} for k in ('stars','forks','open_issues','score','descendants','likes') if isinstance(a.get(k),(int,float)) and isinstance(b.get(k),(int,float))}
                        trend['quality']='stale' if datetime.now(timezone.utc)-end>timedelta(days=2) else 'observed_7d_baseline'
                        trend['baseline_at']=base['captured_at'];trend['latest_at']=latest['captured_at']
                except (ValueError,TypeError):trend['quality']='invalid_timestamp'
            return {'item':found[0],'snapshots':snapshots,'trend':trend,'same_target':rows(c,'SELECT id,source_key,title FROM intel_items WHERE target_key=? AND id<>? ORDER BY id LIMIT 100',(found[0]['target_key'],rid))}
    def item_save(self,rid,p,key):
        with connection(self.database,write=True) as c:
            body={'id':rid,'body':p.model_dump()};prior=replay(c,'intel.item',key,body)
            current=rows(c,'SELECT * FROM intel_items WHERE id=?',(rid,))
            if not current:raise HubError('LOCAL_RECORD_NOT_FOUND',404)
            if prior:return current[0]
            if current[0]['version']!=p.expected_version:raise HubError('VERSION_CONFLICT')
            c.exec_driver_sql('UPDATE intel_items SET domain=?,favorite=?,read_state=?,version=version+1,updated_at=? WHERE id=?',(p.domain,int(p.favorite),p.read_state,utc_now(),rid))
            record(c,'intel.item',key,body,{'id':rid});return rows(c,'SELECT * FROM intel_items WHERE id=?',(rid,))[0]
    def subscriptions(self):
        with connection(self.database) as c:return {'items':rows(c,'SELECT * FROM intel_subscriptions ORDER BY keyword,id')}
    def subscription_save(self,p,key,rid=None):
        with connection(self.database,write=True) as c:
            body={'id':rid,'body':p.model_dump()};prior=replay(c,'intel.subscription',key,body)
            if prior:return rows(c,'SELECT * FROM intel_subscriptions WHERE id=?',(prior['id'],))[0]
            if c.exec_driver_sql('SELECT id FROM intel_subscriptions WHERE keyword=? AND id<>?',(p.keyword,rid or '')).first():raise HubError('SUBSCRIPTION_EXISTS')
            now=utc_now()
            if rid:
                r=rows(c,'SELECT * FROM intel_subscriptions WHERE id=?',(rid,))
                if not r:raise HubError('LOCAL_RECORD_NOT_FOUND',404)
                if r[0]['version']!=p.expected_version:raise HubError('VERSION_CONFLICT')
                c.exec_driver_sql('UPDATE intel_subscriptions SET keyword=?,enabled=?,version=version+1,updated_at=? WHERE id=?',(p.keyword,int(p.enabled),now,rid))
            else:
                if p.expected_version:raise HubError('VERSION_CONFLICT')
                if c.exec_driver_sql('SELECT count(*) FROM intel_subscriptions').scalar()>=200:raise HubError('LOCAL_CAPACITY')
                rid=str(uuid4());c.exec_driver_sql('INSERT INTO intel_subscriptions VALUES(?,?,?,1,?,?)',(rid,p.keyword,int(p.enabled),now,now))
            record(c,'intel.subscription',key,body,{'id':rid});return rows(c,'SELECT * FROM intel_subscriptions WHERE id=?',(rid,))[0]
    def begin(self,sid,key):
        with connection(self.database,write=True) as c:
            op=replay(c,'intel.collect',key,{'source_id':sid})
            if op:return rows(c,'SELECT * FROM intel_runs WHERE id=?',(op['id'],))[0],None
            sources=rows(c,'SELECT * FROM intel_sources WHERE id=?',(sid,))
            if not sources or not sources[0]['enabled']:raise HubError('SOURCE_DISABLED',409)
            if c.exec_driver_sql("SELECT 1 FROM intel_runs WHERE status='running'").first():raise HubError('COLLECTOR_BUSY')
            latest=c.exec_driver_sql('SELECT started_at FROM intel_runs WHERE source_id=? ORDER BY started_at DESC LIMIT 1',(sid,)).scalar()
            if latest and datetime.now(timezone.utc)-datetime.fromisoformat(latest.replace('Z','+00:00'))<timedelta(seconds=60):raise HubError('COLLECTOR_COOLDOWN')
            rid=str(uuid4());c.exec_driver_sql("INSERT INTO intel_runs(id,source_id,status,started_at,operation_key) VALUES(?,?,'running',?,?)",(rid,sid,utc_now(),key))
            record(c,'intel.collect',key,{'source_id':sid},{'id':rid});return {'id':rid,'status':'running'},sources[0]
    async def collect(self,sid,key):
        run,source=await asyncio.to_thread(self.begin,sid,key)
        if not source:return run
        try:
            async with asyncio.timeout(120):items,payload,errors=await COLLECTORS[source['kind']].collect(source)
            await asyncio.to_thread(self.commit,run['id'],source,items,payload,errors)
        except asyncio.CancelledError:
            await asyncio.to_thread(self.fail,run['id'],'interrupted','COLLECTOR_CANCELLED');raise
        except Exception as e:
            await asyncio.to_thread(self.fail,run['id'],'failed',e.code if isinstance(e,HubError) else 'COLLECTOR_PAYLOAD_INVALID')
        return await asyncio.to_thread(self.run,run['id'])
    def fail(self,rid,status,code):
        with connection(self.database,write=True) as c:c.exec_driver_sql("UPDATE intel_runs SET status=?,error_code=?,finished_at=? WHERE id=? AND status='running'",(status,code,utc_now(),rid))
    def run(self,rid):
        with connection(self.database) as c:
            r=rows(c,'SELECT id,source_id,status,started_at,finished_at,received,saved,error_code FROM intel_runs WHERE id=?',(rid,))
            if not r:raise HubError('LOCAL_RECORD_NOT_FOUND',404)
            return r[0]
    def runs(self):
        with connection(self.database) as c:return {'items':rows(c,'SELECT id,source_id,status,started_at,finished_at,received,saved,error_code FROM intel_runs ORDER BY started_at DESC LIMIT 100')}
    @staticmethod
    def upsert(c,source,item,run,at,dedup=None,raw_available=True):
        rid=stable(source,item['remote_id']);url=target(item['url'])
        if not url:raise HubError('COLLECTOR_URL_INVALID',422)
        old=c.exec_driver_sql('SELECT id FROM intel_items WHERE id=?',(rid,)).first()
        if old:c.exec_driver_sql('UPDATE intel_items SET title=?,url=?,target_key=?,summary=?,version=version+1,updated_at=? WHERE id=?',(item['title'][:500],url,url,item['summary'][:10000],at,rid))
        else:c.exec_driver_sql('INSERT INTO intel_items(id,source_key,remote_id,title,url,target_key,summary,version,created_at,updated_at) VALUES(?,?,?,?,?,?,?,1,?,?)',(rid,source,str(item['remote_id']),item['title'][:500],url,url,item['summary'][:10000],at,at))
        if source!='github:legacy' and (not dedup or not c.exec_driver_sql('SELECT 1 FROM intel_snapshots WHERE dedup_key=?',(dedup,)).first()):
            c.exec_driver_sql('INSERT INTO intel_snapshots VALUES(?,?,?,?,?,?,?,?)',(str(uuid4()),rid,run,at,compact(item['raw']),compact(item['metrics']),int(raw_available),dedup))
        return rid
    def commit(self,rid,source,items,payload,errors):
        if len(items)>200 or len(payload.encode())>3*1024*1024:raise HubError('COLLECTOR_ITEM_LIMIT',422)
        with connection(self.database,write=True) as c:
            saved=0;seen=set();at=utc_now()
            for item in items:
                if item['remote_id'] in seen:continue
                seen.add(item['remote_id'])
                self.upsert(c,source['kind']+':'+source['id'],item,rid,at);saved+=1
            c.exec_driver_sql("UPDATE intel_runs SET status=?,finished_at=?,received=?,saved=?,payload_text=?,error_code=? WHERE id=?",('partial' if errors else 'success',at,len(items),saved,payload,'PARTIAL_ITEM_FAILURE' if errors else None,rid))
    def import_legacy(self,key):
        path=self.settings.legacy_database
        if not path.is_file() or any(p.is_symlink() for p in [path,*path.parents]):raise HubError('LEGACY_UNAVAILABLE',503)
        with closing(sqlite3.connect(path.absolute().as_uri()+'?mode=ro',uri=True)) as old:
            old.row_factory=sqlite3.Row;old.execute('PRAGMA query_only=ON');old.execute('BEGIN')
            if sum(old.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in ['repos','snapshots','collection_runs','collection_errors'])>500000:raise HubError('LEGACY_IMPORT_LIMIT',413)
            data={t:[dict(r) for r in old.execute('SELECT * FROM '+t+' ORDER BY id')] for t in ['repos','snapshots','collection_runs','collection_errors']}
            if sum(len(v) for v in data.values())>500000 or len(compact(data).encode())>128*1024*1024:raise HubError('LEGACY_IMPORT_LIMIT',413)
        digest=request_hash(data)
        with connection(self.database,write=True) as c:
            prior=replay(c,'intel.legacy',key,{})
            if prior:return prior
            exists=c.exec_driver_sql('SELECT id FROM intel_legacy_batches WHERE digest=?',(digest,)).first()
            if exists:result={'id':exists[0],'status':'already_imported','counts':{k:len(v) for k,v in data.items()}}
            else:
                batch=str(uuid4());now=utc_now();c.exec_driver_sql('INSERT INTO intel_legacy_batches VALUES(?,?,?,?)',(batch,digest,now,compact({k:len(v) for k,v in data.items()})))
                for table,items in data.items():
                    for item in items:c.exec_driver_sql('INSERT INTO intel_legacy_rows VALUES(?,?,?,?,?)',(batch,table,str(item['id']),stable('legacy:'+table,item['id']),compact(item)))
                repos={r['id']:r for r in data['repos']}
                for item in data['repos']:
                    self.upsert(c,'github:legacy',{'remote_id':str(item['id']),'title':item['full_name'],'url':item['html_url'] or 'https://github.com/'+item['full_name'],'summary':item.get('description') or '', 'metrics':{},'raw':None},None,now,'legacy-repo:'+request_hash(item),False)
                for s in data['snapshots']:
                    if s['repo_id'] not in repos:raise HubError('LEGACY_RELATION_INVALID',422)
                    dedup='legacy-snapshot:'+request_hash(s)
                    if c.exec_driver_sql('SELECT 1 FROM intel_snapshots WHERE dedup_key=?',(dedup,)).first():continue
                    c.exec_driver_sql('INSERT INTO intel_snapshots VALUES(?,?,?,?,?,?,?,?)',(str(uuid4()),stable('github:legacy',s['repo_id']),None,s['captured_at'],compact(None),compact({k:s[k] for k in ['stars','forks','open_issues']}),0,dedup))
                result={'id':batch,'status':'imported','counts':{k:len(v) for k,v in data.items()}}
            record(c,'intel.legacy',key,{},result);return result
    def weekly(self):
        since=(datetime.now(timezone.utc)-timedelta(days=7)).isoformat().replace('+00:00','Z')
        with connection(self.database) as c:
            found=rows(c,"SELECT * FROM intel_items WHERE updated_at>=? AND (favorite=1 OR read_state='later' OR EXISTS(SELECT 1 FROM intel_subscriptions s WHERE s.enabled=1 AND instr(lower(title||' '||summary),lower(s.keyword))>0)) ORDER BY source_key,updated_at DESC,id LIMIT 100",(since,))
            return {'markdown':'# 技术情报周报（人工模板）\n\n口径：最近七天更新、收藏/稍后读/订阅命中，最多100条；不合并热度。\n\n'+'\n'.join('- '+r['source_key']+' · '+r['title'].replace('\n',' ')+'\n  '+r['url'] for r in found),'count':len(found)}
    def recover(self):
        with connection(self.database,write=True) as c:c.exec_driver_sql("UPDATE intel_runs SET status='interrupted',finished_at=?,error_code='PROCESS_INTERRUPTED' WHERE status='running'",(utc_now(),))
