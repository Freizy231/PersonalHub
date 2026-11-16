"""Plans and actual workouts are separate facts; linked Task/Event have one authority."""
from ....core.database import connection,HubError
from ...knowledge.service import rows,visible
from ..finance.schemas import month_valid,month_bounds
from ....core.local_records import save,get
class ExerciseService:
    def __init__(self,database):self.database=database
    @staticmethod
    def safe(con,r):
        r=dict(r)
        for col,kind in [('task_id','task'),('event_id','event')]:
            if r.get(col):
                try:
                    ref=visible(con,r[col])
                    if ref['kind']!=kind:raise HubError('RESOURCE_NOT_FOUND',404)
                    r[col.removesuffix('_id')]={'id':ref['id'],'title':ref['title']}
                except HubError:r[col]=None;r[col.removesuffix('_id')]=None
        if r.get('plan_id'):
            plan=get(con,'exercise_plans',r['plan_id'])
            if plan['deleted_at']:r['plan_id']=None
        return r
    def save(self,table,p,key,rid=None):
        assert table in {'exercise_plans','exercise_records'}
        body=p.model_dump(mode='json');v={k:x for k,x in body.items() if k!='expected_version'}
        if 'cancelled' in v:v['cancelled']=int(v['cancelled'])
        with connection(self.database,write=True) as con:
            for col,kind in [('task_id','task'),('event_id','event')]:
                if v.get(col) and visible(con,v[col])['kind']!=kind:raise HubError('EXERCISE_LINK_INVALID',422)
            if v.get('plan_id') and get(con,'exercise_plans',v['plan_id'])['deleted_at']:raise HubError('LOCAL_RECORD_NOT_FOUND',404)
            return self.safe(con,save(con,table,v,p.expected_version,key,body,rid))
    def month(self,month,trash=False):
        month_valid(month)
        with connection(self.database) as con:
            result={}
            for name,table in [('plans','exercise_plans'),('records','exercise_records')]:
                found=rows(con,f'SELECT * FROM {table} WHERE local_date>=? AND local_date<? AND (deleted_at IS NOT NULL)=? ORDER BY local_date,id LIMIT 1001',month_bounds(month)+(int(trash),))
                if len(found)>1000:raise HubError('EXERCISE_MONTH_CAPACITY',422)
                result[name]=[self.safe(con,x) for x in found]
            result['statistics']=rows(con,'SELECT kind,count(*) AS sessions,sum(duration_seconds) AS duration_seconds,sum(distance_meters) AS distance_meters FROM exercise_records WHERE local_date>=? AND local_date<? AND deleted_at IS NULL GROUP BY kind',month_bounds(month))
            result['month']=month
            return result
    def export(self):
        with connection(self.database) as con:return {'format':'personal-hub-exercise','version':1,**{name:[self.safe(con,x) for x in rows(con,f'SELECT * FROM {table} ORDER BY id')] for name,table in [('plans','exercise_plans'),('records','exercise_records')]}}
