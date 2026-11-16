"""Bounded hash-only cache; every lookup securely opens and checks fresh inode metadata."""
from collections import OrderedDict
import hashlib,os,stat,threading
from .database import HubError
from .note_files import parts,MAX_BYTES
_cache=OrderedDict();_lock=threading.Lock();MAX_ENTRIES=8192

def verify(store,relative,expected):
 if relative.startswith('library/'):
  from ..modules.library.documents.storage import blob_parts,MAX_BLOB,MAX_TEXT
  p=blob_parts(relative);maximum=MAX_TEXT if p[1]=='text' else MAX_BLOB
 else:p=parts(relative);maximum=MAX_BYTES
 try:
  with store.folder(p[:-1]) as folder:
   fd=os.open(p[-1],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=folder)
   with os.fdopen(fd,'rb') as file:
    info=os.fstat(file.fileno())
    if not stat.S_ISREG(info.st_mode) or info.st_mode&0o077 or info.st_size>maximum:raise HubError('FILE_INTEGRITY_INVALID')
    stamp=(info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns)
    key=(str(store.root),relative)
    with _lock:
     cached=_cache.get(key)
     if cached==(stamp,expected):_cache.move_to_end(key);return True
    hasher=hashlib.sha256();size=0
    while chunk:=file.read(1024*1024):
     size+=len(chunk)
     if size>maximum:raise HubError('FILE_INTEGRITY_INVALID')
     hasher.update(chunk)
    after=os.fstat(file.fileno())
    if (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns)!=stamp or hasher.hexdigest()!=expected:raise HubError('FILE_INTEGRITY_INVALID')
    with _lock:
     _cache[key]=(stamp,expected);_cache.move_to_end(key)
     while len(_cache)>MAX_ENTRIES:_cache.popitem(last=False)
    return True
 except OSError:raise HubError('FILE_INTEGRITY_INVALID') from None
