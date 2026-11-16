"""Immutable, separately budgeted PDF/text/staging files; never accepts user paths."""
import hashlib,os,stat
from pathlib import PurePosixPath
from ....core.note_files import NoteFiles,digest
from ....core.database import HubError
MAX_PDF=100*1024*1024
MAX_TEXT=16*1024*1024
# EPUB is a zip archive; the cap equals the PDF cap so one budget covers both.
MAX_EPUB=100*1024*1024
MAX_BLOB=max(MAX_PDF,MAX_EPUB)

def blob_parts(relative):
 if not isinstance(relative,str) or '\\' in relative or '\x00' in relative:raise HubError('BLOB_PATH_UNSAFE')
 p=PurePosixPath(relative)
 if p.is_absolute() or len(p.parts)<3 or p.parts[0]!='library' or p.parts[1] not in ('blobs','staging','text') or any(x in ('','.','..') for x in relative.split('/')):raise HubError('BLOB_PATH_UNSAFE')
 return p.parts

class BlobFiles(NoteFiles):
 def read(self,relative,expected=None):
  p=blob_parts(relative);maximum=MAX_TEXT if p[1]=='text' else MAX_BLOB
  try:
   with self.folder(p[:-1]) as folder:
    fd=os.open(p[-1],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=folder)
    with os.fdopen(fd,'rb') as f:
     info=os.fstat(f.fileno())
     if not stat.S_ISREG(info.st_mode) or info.st_mode&0o077 or info.st_size>maximum:raise HubError('BLOB_FILE_UNSAFE')
     data=f.read(maximum+1)
   if len(data)>maximum:raise HubError('BLOB_FILE_UNSAFE')
  except FileNotFoundError:raise HubError('BLOB_FILE_MISSING') from None
  except OSError:raise HubError('BLOB_FILE_UNSAFE') from None
  if expected is not None and digest(data)!=expected:raise HubError('BLOB_FILE_CONFLICT')
  return data
 def create(self,relative):
  p=blob_parts(relative)
  with self.folder(p[:-1],create=True) as fd:
   descriptor=os.open(p[-1],os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600,dir_fd=fd);os.fsync(fd)
   return descriptor
 def publish(self,staging,relative,expected):
  data=self.read(staging,expected);dest=blob_parts(relative);src=blob_parts(staging)
  with self.folder(src[:-1]) as sf,self.folder(dest[:-1],create=True) as df:
   try:os.link(src[-1],dest[-1],src_dir_fd=sf,dst_dir_fd=df,follow_symlinks=False);os.fsync(df)
   except FileExistsError:self.read(relative,expected)
  # Preserve staging as recovery evidence; explicit maintenance can quarantine later.
  return len(data)
 def write_text(self,relative,data):
  if len(data)>MAX_TEXT:raise HubError('PDF_TEXT_BUDGET')
  fd=self.create(relative)
  with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
  return digest(data)
 def inventory(self):
  result={}
  if not (self.root/'library').exists() and not (self.root/'library').is_symlink():return result
  def walk(relative):
   # library root itself has no file leaves.
   p=PurePosixPath(relative).parts
   with self.folder(p) as fd:
    for name in sorted(os.listdir(fd)):
     info=os.stat(name,dir_fd=fd,follow_symlinks=False);child=relative+'/'+name
     if stat.S_ISDIR(info.st_mode):
      if relative=='library' and name not in ('blobs','staging','text'):raise HubError('BLOB_PATH_UNSAFE')
      walk(child)
     elif stat.S_ISREG(info.st_mode):
      data=self.read(child);result[child]={'sha256':digest(data),'size':len(data)}
     else:raise HubError('BLOB_FILE_UNSAFE')
  walk('library');return result
 def discard_committed_staging(self,relative,expected):
  p=blob_parts(relative)
  if p[1]!='staging':raise HubError('BLOB_PATH_UNSAFE')
  self.read(relative,expected)
  with self.folder(p[:-1]) as folder:os.unlink(p[-1],dir_fd=folder);os.fsync(folder)
