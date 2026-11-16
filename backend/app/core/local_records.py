"""Internal CAS/idempotency helpers for fixed, non-Resource record tables."""
from uuid import uuid4
from .database import connection,HubError,utc_now
from .operations import record,replay

def rows(con,sql,args=()):return [dict(r) for r in con.exec_driver_sql(sql,args).mappings()]

# Shared internal table writer, never accepts a table/column from an HTTP request.
TABLES={'finance_categories','finance_entries','finance_budgets','exercise_plans','exercise_records'}
def get(con,table,rid):
    assert table in TABLES
    found=rows(con,f'SELECT * FROM {table} WHERE id=?',(rid,))
    if not found:raise HubError('LOCAL_RECORD_NOT_FOUND',404)
    return found[0]

def save(con,table,values,expected,key,payload,rid=None):
    assert table in TABLES
    op=table+('.update' if rid else '.create')
    fingerprint={'id':rid,'body':payload}
    previous=replay(con,op,key,fingerprint)
    if previous:return get(con,table,previous['id'])
    now=utc_now()
    if rid:
        old=get(con,table,rid)
        if old['version']!=expected or old.get('deleted_at'):raise HubError('VERSION_CONFLICT')
        con.exec_driver_sql(f"UPDATE {table} SET "+','.join(f'{k}=?' for k in values)+',version=version+1,updated_at=? WHERE id=?',tuple(values.values())+(now,rid))
    else:
        if expected!=0:raise HubError('VERSION_CONFLICT')
        rid=str(uuid4());values={'id':rid,**values,'version':1,'created_at':now,'updated_at':now}
        con.exec_driver_sql(f"INSERT INTO {table} ("+','.join(values)+') VALUES ('+','.join('?' for _ in values)+')',tuple(values.values()))
    record(con,op,key,fingerprint,{'id':rid})
    return get(con,table,rid)

def state(database,table,rid,p,key,restore):
    assert table in TABLES and table!='finance_categories' and table!='finance_budgets'
    with connection(database,write=True) as con:
        body={'id':rid,'version':p.expected_version,'restore':restore};op=table+'.state'
        if replay(con,op,key,body):return get(con,table,rid)
        old=get(con,table,rid)
        if old['version']!=p.expected_version:raise HubError('VERSION_CONFLICT')
        if bool(old['deleted_at'])!=bool(restore):raise HubError('LOCAL_RECORD_STATE_CONFLICT',409)
        now=utc_now();con.exec_driver_sql(f'UPDATE {table} SET deleted_at=?,updated_at=?,version=version+1 WHERE id=?',(None if restore else now,now,rid))
        record(con,op,key,body,{'id':rid});return get(con,table,rid)

