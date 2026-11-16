"""Independent source adapters. Each fetched payload is retained verbatim, never execute HTML."""
import asyncio,json,re,base64,hashlib
import xml.etree.ElementTree as ET
from urllib.parse import urlsplit,urlunsplit
from ...core.outbound import fetch,public_url
from ...core.database import HubError

def target(url):
    try:
        p=urlsplit(url)
        if p.scheme not in ('http','https') or not p.hostname or p.username or p.password:return ''
        # Keep query semantics and path case; strip only fragment and default ports.
        host=p.hostname.lower();port=p.port
        if ':' in host:host='['+host+']'
        if port and not (p.scheme=='https' and port==443 or p.scheme=='http' and port==80):host+=':'+str(port)
        return urlunsplit((p.scheme,host,p.path or '/',p.query,''))
    except ValueError:return ''

def text(node,name,ns=''):
    n=node.find(ns+name)
    return ''.join(n.itertext()).strip() if n is not None else ''

def xml_items(payload):
    if b'\x00' in payload or b'<!DOCTYPE' in payload.upper() or b'<!ENTITY' in payload.upper():raise HubError('FEED_XML_UNSAFE',422)
    try:root=ET.fromstring(payload)
    except ET.ParseError:raise HubError('FEED_XML_INVALID',422) from None
    atom=root.tag=='{http://www.w3.org/2005/Atom}feed';ns='{http://www.w3.org/2005/Atom}' if atom else ''
    nodes=root.findall(ns+'entry') if atom else root.findall('./channel/item')
    if not atom and root.tag!='rss':raise HubError('FEED_FORMAT_UNSUPPORTED',422)
    if len(nodes)>200:raise HubError('COLLECTOR_ITEM_LIMIT',422)
    result=[]
    for n in nodes:
        url=text(n,'link',ns)
        if atom:
            links=n.findall(ns+'link');url=next((x.attrib.get('href','') for x in links if x.attrib.get('rel','alternate')=='alternate'),'')
        rid=text(n,'id' if atom else 'guid',ns) or url
        title=text(n,'title',ns)
        if not rid or not title or not target(url):continue
        result.append({'remote_id':rid,'title':title,'url':url,'summary':text(n,'summary' if atom else 'description',ns) or text(n,'content',ns),'metrics':{},'raw':ET.tostring(n,encoding='unicode')})
    return result

def with_query(source):
    """The endpoint to fetch for this source.

    A query typed into the source form is appended to the adapter's own URL shape where the
    provider accepts one (arXiv), and is used as the feed address for RSS. An empty query
    falls back to the adapter's default list, never to an arbitrary URL.
    """
    kind=source.get('kind');query=(source.get('query') or '').strip()
    if not query:return source['endpoint']
    if kind=='arxiv':
        from urllib.parse import quote
        return ('https://export.arxiv.org/api/query?search_query='+quote(query,safe=':+"()')+
                '&start=0&max_results=50&sortBy=submittedDate&sortOrder=descending')
    return source['endpoint']

class RSSCollector:
    async def collect(self,source):
        payload=await fetch(with_query(source));items=xml_items(payload)
        # Retain the original complete payload once per run as well as normalized entries.
        return items,json.dumps({'encoding':'base64','sha256':hashlib.sha256(payload).hexdigest(),'payload':base64.b64encode(payload).decode('ascii')}),[]

class ArxivCollector(RSSCollector):
    async def collect(self,source):return await super().collect(source)

class HFCollector:
    async def collect(self,source):
        payload=await fetch('https://huggingface.co/api/models?sort=trendingScore&limit=50&full=false')
        try:
            data=json.loads(payload)
            if not isinstance(data,list) or len(data)>200:raise ValueError()
            found=[]
            for x in data:
                if not isinstance(x,dict) or not isinstance(x.get('id'),str):raise ValueError()
                metrics={k:x[k] for k in ('likes','downloads','trendingScore') if isinstance(x.get(k),(int,float)) and not isinstance(x[k],bool)}
                found.append({'remote_id':x['id'],'title':x['id'],'url':'https://huggingface.co/'+x['id'],'summary':', '.join(x.get('tags',[])[:30]),'metrics':metrics,'raw':x})
            return found,payload.decode(),[]
        except (ValueError,TypeError,KeyError):raise HubError('COLLECTOR_PAYLOAD_INVALID',422) from None

class HNCollector:
    async def collect(self,source):
        raw=await fetch('https://hacker-news.firebaseio.com/v0/topstories.json')
        try:
            ids=json.loads(raw)
            if not isinstance(ids,list) or not all(isinstance(n,int) and not isinstance(n,bool) and n>0 for n in ids):raise ValueError()
        except ValueError:raise HubError('COLLECTOR_PAYLOAD_INVALID',422) from None
        found=[];errors=[];payloads={'topstories':ids}
        query=(source.get('query') or '').strip().lower()
        words=[w for w in query.split() if w]
        # Sequential request budget; a failed item makes a partial run.
        for rid in ids[:50 if words else 20]:
            try:
                item=json.loads(await fetch(f'https://hacker-news.firebaseio.com/v0/item/{rid}.json'))
                payloads[str(rid)]=item
                if not isinstance(item,dict):raise HubError('COLLECTOR_PAYLOAD_INVALID',422)
                if item.get('deleted') or item.get('dead'):continue
                if not isinstance(item.get('title'),str):raise HubError('COLLECTOR_PAYLOAD_INVALID',422)
                if words and not all(w in (item['title'] or '').lower() or w in (item.get('text') or '').lower() for w in words):
                    continue   # the person asked for these words only
                found.append({'remote_id':str(rid),'title':item['title'],'url':item.get('url') or f'https://news.ycombinator.com/item?id={rid}',
                    'summary':item.get('text',''),'metrics':{k:item[k] for k in ('score','descendants') if isinstance(item.get(k),int)},'raw':item})
            except (HubError,ValueError):errors.append('ITEM_FETCH_FAILED')
        return found,json.dumps(payloads,ensure_ascii=False),errors

COLLECTORS={'rss':RSSCollector(),'arxiv':ArxivCollector(),'hn':HNCollector(),'huggingface':HFCollector()}
DEFAULTS={'hn':'https://hacker-news.firebaseio.com/v0/topstories.json','arxiv':'https://export.arxiv.org/api/query?search_query=cat:cs.AI&start=0&max_results=50&sortBy=submittedDate&sortOrder=descending','huggingface':'https://huggingface.co/api/models?sort=trendingScore&limit=50&full=false'}
