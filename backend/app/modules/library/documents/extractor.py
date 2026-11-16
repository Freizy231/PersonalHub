"""Installed Poppler adapter. Bounded child processes, no shell, no secret argv."""
import os,signal,subprocess,tempfile,sys
from pathlib import Path
from ....core.database import HubError
MAX_PAGES=2000
MAX_TEXT=16*1024*1024

def run(args,cancel=None):
 # Files bound output at the OS layer; never buffer unbounded child output in RAM.
 with tempfile.TemporaryFile() as out,tempfile.TemporaryFile() as err:
  proc=subprocess.Popen([sys.executable,'-I',str(Path(__file__).with_name('parser_exec.py')),*args],stdin=subprocess.DEVNULL,stdout=out,stderr=err,shell=False,
      env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','HOME':'/nonexistent'},start_new_session=True)
  import time
  deadline=time.monotonic()+30
  while proc.poll() is None:
   if (cancel and cancel()) or time.monotonic()>deadline:
    os.killpg(proc.pid,signal.SIGKILL);proc.wait()
    raise HubError('PDF_CANCELLED' if cancel and cancel() else 'PDF_TIMEOUT')
   try:proc.wait(timeout=.1)
   except subprocess.TimeoutExpired:pass
  out.seek(0);err.seek(0);data=out.read(MAX_TEXT+1);errors=err.read(4096)
  if len(data)>MAX_TEXT:raise HubError('PDF_TEXT_BUDGET')
  if proc.returncode:
   if b'HUB_PDF_SANDBOX_UNAVAILABLE' in errors:raise HubError('PDF_SANDBOX_UNAVAILABLE')
   if b'password' in errors.lower():raise HubError('PDF_PASSWORD_REQUIRED')
   if proc.returncode<0:raise HubError('PDF_RESOURCE_BUDGET')
   raise HubError('PDF_INVALID')
  return data

def extract(path,cancel=None):
 if not Path('/usr/bin/pdfinfo').is_file() or not Path('/usr/bin/pdftotext').is_file():raise HubError('PDF_EXTRACTOR_UNAVAILABLE')
 info=run(['/usr/bin/pdfinfo','-enc','UTF-8',str(path)],cancel).decode('utf-8',errors='replace')
 fields={}
 for line in info.splitlines():
  if ':' in line:k,v=line.split(':',1);fields[k.strip()]=v.strip()[:2000]
 try:pages=int(fields['Pages'])
 except (ValueError,KeyError):raise HubError('PDF_INVALID') from None
 if pages>MAX_PAGES:raise HubError('PDF_PAGE_BUDGET')
 if pages<1:raise HubError('PDF_INVALID')
 body=run(['/usr/bin/pdftotext','-enc','UTF-8','-nopgbrk',str(path),'-'],cancel)
 return {'page_count':pages,'title':fields.get('Title',''),'author':fields.get('Author',''),
         'subject':fields.get('Subject',''),'created':fields.get('CreationDate',''),'text':body}
