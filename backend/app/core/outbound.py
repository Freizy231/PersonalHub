"""Bounded HTTPS without proxies, redirects or access to non-public addresses."""
import asyncio,ipaddress,socket
from urllib.parse import urlsplit
import httpx
from .database import HubError

def public_url(value):
    try:
        u=urlsplit(value)
        if u.scheme!='https' or not u.hostname or u.username or u.password or u.fragment or (u.port or 443)!=443 or len(value)>2048:raise ValueError()
        if u.hostname.lower()=='localhost' or u.hostname.endswith('.local'):raise ValueError()
        try:
            if not ipaddress.ip_address(u.hostname).is_global:raise ValueError()
        except ValueError:
            # A syntactically valid IP must not be mistaken for a DNS name.
            try:ipaddress.ip_address(u.hostname)
            except ValueError:pass
            else:raise
        return value
    except ValueError:raise HubError('OUTBOUND_URL_INVALID',422) from None

def is_public_address(value):
    """True only for a globally routable address.

    `is_global` alone accepts NAT64 (64:ff9b::/96) and 6to4 (2002::/16): on a network that
    translates them, such an address reaches a private or loopback IPv4. For those prefixes the
    embedded IPv4 is checked instead, and Teredo (2001::/32) is refused outright.
    """
    ip = ipaddress.ip_address(value)
    if ip.version == 6:
        if ip.ipv4_mapped is not None:
            return ip.ipv4_mapped.is_global
        packed = ip.packed
        if ip in ipaddress.ip_network('64:ff9b::/96'):
            return ipaddress.IPv4Address(packed[12:]).is_global
        if ip in ipaddress.ip_network('2002::/16'):
            return ipaddress.IPv4Address(packed[2:6]).is_global
        if ip in ipaddress.ip_network('2001::/32'):
            return False
    return ip.is_global


class PublicTransport(httpx.AsyncBaseTransport):
    def __init__(self):self.inner=httpx.AsyncHTTPTransport(retries=0,trust_env=False)
    async def handle_async_request(self,request):
        public_url(str(request.url));host=request.url.host
        infos=await asyncio.get_running_loop().getaddrinfo(host,443,type=socket.SOCK_STREAM)
        addresses=list(dict.fromkeys(i[4][0] for i in infos))
        if not addresses or any(not is_public_address(a) for a in addresses):raise HubError('OUTBOUND_ADDRESS_DENIED',422)
        # Pin the socket destination while retaining certificate verification/SNI and Host.
        pinned=httpx.Request(request.method,request.url.copy_with(host=addresses[0]),headers=request.headers,stream=request.stream,
            extensions={**request.extensions,'sni_hostname':host})
        return await self.inner.handle_async_request(pinned)
    async def aclose(self):await self.inner.aclose()

async def fetch(url,*,body=None,token=None,max_bytes=2*1024*1024):
    public_url(url)
    headers={'User-Agent':'PersonalHub/0.12 (personal local reader)','Accept':'application/json, application/atom+xml, application/rss+xml, text/xml'}
    if token:headers['Authorization']='Bearer '+token
    try:
        async with asyncio.timeout(45):
            async with httpx.AsyncClient(transport=PublicTransport(),trust_env=False,follow_redirects=False,timeout=httpx.Timeout(30,connect=10)) as client:
                async with client.stream('POST' if body is not None else 'GET',url,json=body,headers=headers) as response:
                    if response.status_code==429:raise HubError('OUTBOUND_RATE_LIMITED',503)
                    if response.status_code<200 or response.status_code>=300:raise HubError('OUTBOUND_HTTP_FAILED',503)
                    chunks=[];size=0
                    async for chunk in response.aiter_bytes():
                        size+=len(chunk)
                        if size>max_bytes:raise HubError('OUTBOUND_RESPONSE_LIMIT',413)
                        chunks.append(chunk)
                    return b''.join(chunks)
    except (httpx.HTTPError,TimeoutError,OSError):raise HubError('OUTBOUND_UNAVAILABLE',503) from None
