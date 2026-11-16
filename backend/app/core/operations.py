import hashlib,json,uuid
from sqlalchemy import select
from .database import HubError,utc_now
from .resources import activities,operation_requests


def request_hash(payload):
    return hashlib.sha256(json.dumps(payload,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def replay(con,kind,key,payload):
    row=con.execute(select(operation_requests).where(operation_requests.c.idempotency_key==key)).mappings().first()
    if not row:return None
    if row['operation_kind']!=kind or row['request_hash']!=request_hash(payload):raise HubError('IDEMPOTENCY_CONFLICT')
    return json.loads(row['result_json'])


def record(con,kind,key,payload,result):
    instant=utc_now()
    con.execute(operation_requests.insert().values(id=str(uuid.uuid4()),idempotency_key=key,request_hash=request_hash(payload),
        operation_kind=kind,status='applied',result_json=json.dumps(result),expires_at=None,created_at=instant,updated_at=instant))


def activity(con,resource_id,action,fields):
    con.execute(activities.insert().values(id=str(uuid.uuid4()),resource_id=resource_id,action=action,actor='user',
        occurred_at=utc_now(),privacy_level='personal',payload_json=json.dumps({'fields':sorted(fields)})))
