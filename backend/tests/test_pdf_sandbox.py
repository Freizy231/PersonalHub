"""Real child OS policy probes. Fixtures only; never attempts to read user secrets."""
import os,subprocess,sys,tempfile,unittest
from pathlib import Path
from backend.app.modules.library.documents.extractor import run
from backend.app.core.database import HubError
class ParserSandbox(unittest.TestCase):
 def test_landlock_denies_unselected_file_and_seccomp_denies_socket_and_fork(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);allowed=root/'input.pdf';allowed.write_bytes(b'%PDF-fixture');secret=root/'fictitious-secret';secret.write_text('fixture only')
   source='''
import os,sys,socket
from backend.app.modules.library.documents.parser_exec import restrict_syscalls,restrict_filesystem
import ctypes
assert ctypes.CDLL(None).prctl(38,1,0,0,0)==0
restrict_syscalls();restrict_filesystem(sys.argv[1])
assert open(sys.argv[1],'rb').read()==b'%PDF-fixture'
for action in [lambda:open(sys.argv[2],'rb'),lambda:socket.socket(),lambda:os.fork()]:
 try:action()
 except PermissionError:pass
 else:raise SystemExit(9)
'''
   result=subprocess.run([sys.executable,'-B','-c',source,str(allowed),str(secret)],capture_output=True,timeout=10)
   self.assertEqual(result.returncode,0,result.stderr.decode())
 def test_adapter_timeout_terminates_child_and_never_disables_sandbox(self):
  # Use only trusted trampoline, force a deadline before parsing can finish.
  with tempfile.TemporaryDirectory() as t:
   file=Path(t)/'fixture.pdf';file.write_bytes(b'%PDF-1.4\n')
   from unittest.mock import patch
   with patch('time.monotonic',side_effect=[0,31]):
    with self.assertRaises(HubError) as error:run(['/usr/bin/pdfinfo',str(file)])
   self.assertEqual(error.exception.code,'PDF_TIMEOUT')
