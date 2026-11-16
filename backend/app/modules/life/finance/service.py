"""Private ledger: no Resource, Activity, FTS, AI or ordinary ZIP projection."""
import json
from uuid import uuid4
from ....core.database import connection,HubError,utc_now
from ....core.operations import record,replay
from ...knowledge.service import rows
from .schemas import money,month_valid,month_bounds

from ....core.local_records import get,save,state

class FinanceService:
    def __init__(self,database):self.database=database
    def categories(self):
        with connection(self.database) as con:return {'items':rows(con,'SELECT * FROM finance_categories ORDER BY direction,name,id')}
    def category_save(self,p,key,rid=None):
        with connection(self.database,write=True) as con:
            previous=replay(con,'finance_categories'+('.update' if rid else '.create'),key,{'id':rid,'body':p.model_dump(mode='json')})
            if previous:return get(con,'finance_categories',previous['id'])
            if rid and get(con,'finance_categories',rid)['direction']!=p.direction:raise HubError('CATEGORY_DIRECTION_IMMUTABLE')
            exists=con.exec_driver_sql('SELECT id FROM finance_categories WHERE name=? AND direction=?',(p.name,p.direction)).first()
            if exists and exists[0]!=rid:raise HubError('CATEGORY_EXISTS')
            if not rid and con.exec_driver_sql('SELECT count(*) FROM finance_categories').scalar()>=500:raise HubError('LOCAL_CAPACITY')
            return save(con,'finance_categories',{'name':p.name,'direction':p.direction},p.expected_version,key,p.model_dump(mode='json'),rid)
    def save(self,table,p,key,rid=None):
        assert table in {'finance_entries','finance_budgets'}
        values=p.values()
        with connection(self.database,write=True) as con:
            previous=replay(con,table+('.update' if rid else '.create'),key,{'id':rid,'body':p.model_dump(mode='json')})
            if previous:return self.present(get(con,table,previous['id']))
            cat=get(con,'finance_categories',str(p.category_id))
            if table=='finance_budgets':
                if cat['direction']!='expense':raise HubError('BUDGET_EXPENSE_REQUIRED',422)
                existing=con.exec_driver_sql('SELECT id FROM finance_budgets WHERE month=? AND category_id=? AND currency=?',(p.month,str(p.category_id),p.currency)).first()
                if existing and existing[0]!=rid:raise HubError('BUDGET_EXISTS')
            return self.present(save(con,table,values,p.expected_version,key,p.model_dump(mode='json'),rid))
    @staticmethod
    def present(r):return {**{k:v for k,v in r.items() if k!='amount_minor'},'amount':money(r['amount_minor'],r['currency'])}
    def listing(self,month,trash=False,limit=50,offset=0):
        month_valid(month)
        with connection(self.database) as con:
            args=month_bounds(month)+(int(trash),);clause="e.local_date>=? AND e.local_date<? AND (e.deleted_at IS NOT NULL)=?"
            count=con.exec_driver_sql('SELECT count(*) FROM finance_entries e WHERE '+clause,args).scalar()
            found=rows(con,'SELECT e.*,c.name AS category_name,c.direction FROM finance_entries e JOIN finance_categories c ON c.id=e.category_id WHERE '+clause+' ORDER BY e.local_date DESC,e.created_at DESC,e.id LIMIT ? OFFSET ?',args+(limit,offset))
            return {'items':[self.present(x) for x in found],'total':count,'offset':offset,'limit':limit}
    def report(self,month):
        month_valid(month)
        with connection(self.database) as con:
            entries=rows(con,"SELECT e.currency,e.category_id,c.name,c.direction,sum(e.amount_minor) AS amount_minor FROM finance_entries e JOIN finance_categories c ON c.id=e.category_id WHERE e.local_date>=? AND e.local_date<? AND e.deleted_at IS NULL GROUP BY e.currency,e.category_id",month_bounds(month))
            budgets=rows(con,'SELECT b.*,c.name FROM finance_budgets b JOIN finance_categories c ON c.id=b.category_id WHERE month=? ORDER BY b.currency,c.name',(month,))
            totals={}
            for r in entries:
                t=totals.setdefault(r['currency'],{'income':0,'expense':0});t[r['direction']]+=r['amount_minor']
            bycat={(r['currency'],r['category_id']):r['amount_minor'] for r in entries if r['direction']=='expense'}
            return {'month':month,'totals':[{'currency':c,'income':money(t['income'],c),'expense':money(t['expense'],c),'balance':money(t['income']-t['expense'],c)} for c,t in sorted(totals.items())],
                'categories':[self.present(x) for x in entries], 'budgets':[{**self.present(b),'spent':money(bycat.get((b['currency'],b['category_id']),0),b['currency']),'remaining':money(b['amount_minor']-bycat.get((b['currency'],b['category_id']),0),b['currency'])} for b in budgets]}
    def export(self):
        with connection(self.database) as con:
            return {'format':'personal-hub-private-ledger','version':1,'exported_at':utc_now(),
                'categories':rows(con,'SELECT * FROM finance_categories ORDER BY id'),
                'entries':[self.present(r) for r in rows(con,'SELECT * FROM finance_entries ORDER BY id')],
                'budgets':[self.present(r) for r in rows(con,'SELECT * FROM finance_budgets ORDER BY id')]}

    def import_data(self,p,key):
        """Additive import: identity matches must agree, never overwrite existing records."""
        with connection(self.database,write=True) as con:
            payload=p.model_dump(mode='json');prior=replay(con,'finance.import',key,payload)
            if prior:return prior
            added={};now=utc_now()
            for table,models in [('finance_categories',p.categories),('finance_entries',p.entries),('finance_budgets',p.budgets)]:
                ids=[str(m.id) for m in models]
                if len(set(ids))!=len(ids):raise HubError('FINANCE_IMPORT_CONFLICT',409)
                added[table]=0
                for model in models:
                    rid=str(model.id)
                    if table=='finance_categories':values={'name':model.name,'direction':model.direction}
                    else:
                        values=model.values();cat=get(con,'finance_categories',values['category_id'])
                        if table=='finance_budgets' and cat['direction']!='expense':raise HubError('BUDGET_EXPENSE_REQUIRED',422)
                    old=rows(con,f'SELECT * FROM {table} WHERE id=?',(rid,))
                    if old:
                        if old[0].get('deleted_at') or any(old[0][k]!=v for k,v in values.items()):raise HubError('FINANCE_IMPORT_CONFLICT',409)
                        continue
                    if table=='finance_categories':
                        if con.exec_driver_sql('SELECT 1 FROM finance_categories WHERE name=? AND direction=?',(values['name'],values['direction'])).first():raise HubError('FINANCE_IMPORT_CONFLICT',409)
                    if table=='finance_budgets':
                        if con.exec_driver_sql('SELECT 1 FROM finance_budgets WHERE month=? AND category_id=? AND currency=?',(values['month'],values['category_id'],values['currency'])).first():raise HubError('FINANCE_IMPORT_CONFLICT',409)
                    data={'id':rid,**values,'version':1,'created_at':now,'updated_at':now}
                    con.exec_driver_sql(f'INSERT INTO {table} ('+','.join(data)+') VALUES ('+','.join('?' for _ in data)+')',tuple(data.values()));added[table]+=1
            result={'status':'imported','added':added};record(con,'finance.import',key,payload,result);return result
