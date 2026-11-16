"""Trusted Linux child trampoline: budgets, Landlock filesystem and seccomp network denial."""
import ctypes,ctypes.util,errno,os,resource,sys

def restrict_filesystem(input_file):
 libc=ctypes.CDLL(None,use_errno=True)
 abi=libc.syscall(444,0,0,1) # landlock_create_ruleset(... VERSION)
 if abi<1:raise OSError('landlock unavailable')
 class Ruleset(ctypes.Structure):_fields_=[('handled_access_fs',ctypes.c_uint64)]
 class Beneath(ctypes.Structure):
  _pack_=1
  _fields_=[('allowed_access',ctypes.c_uint64),('parent_fd',ctypes.c_int)]
 handled=(1<<13)-1
 if abi>=2:handled|=1<<13 # REFER
 if abi>=3:handled|=1<<14 # TRUNCATE
 rules=Ruleset(handled);fd=libc.syscall(444,ctypes.byref(rules),ctypes.sizeof(rules),0)
 if fd<0:raise OSError('ruleset unavailable')
 try:
  for path in ['/usr','/lib','/lib64','/etc/ld.so.cache','/etc/fonts','/var/cache/fontconfig',str(input_file)]:
   if not os.path.exists(path):continue
   directory=os.path.isdir(path);access=(1|4|8) if directory else 4
   parent=os.open(path,os.O_PATH|os.O_CLOEXEC)
   try:
    rule=Beneath(access,parent)
    if libc.syscall(445,fd,1,ctypes.byref(rule),0)!=0:raise OSError('landlock rule rejected')
   finally:os.close(parent)
  if libc.syscall(446,fd,0)!=0:raise OSError('landlock restrict rejected')
 finally:os.close(fd)

def restrict_syscalls():
 library=ctypes.util.find_library('seccomp')
 if not library:raise OSError('seccomp unavailable')
 sec=ctypes.CDLL(library);sec.seccomp_init.argtypes=[ctypes.c_uint32];sec.seccomp_init.restype=ctypes.c_void_p
 sec.seccomp_syscall_resolve_name.argtypes=[ctypes.c_char_p];sec.seccomp_syscall_resolve_name.restype=ctypes.c_int
 sec.seccomp_rule_add.argtypes=[ctypes.c_void_p,ctypes.c_uint32,ctypes.c_int,ctypes.c_uint]
 sec.seccomp_load.argtypes=[ctypes.c_void_p];sec.seccomp_release.argtypes=[ctypes.c_void_p]
 ctx=sec.seccomp_init(0x7fff0000) # ALLOW default; filesystem already constrained with Landlock.
 if not ctx:raise OSError('seccomp init rejected')
 try:
  for name in ['socket','socketpair','connect','bind','listen','accept','accept4','sendto','sendmsg','recvfrom','recvmsg','ptrace','process_vm_readv','process_vm_writev','mount','umount2','unshare','setns','bpf','fork','vfork','clone','clone3','io_uring_setup','open_by_handle_at']:
   number=sec.seccomp_syscall_resolve_name(name.encode())
   if number>=0 and sec.seccomp_rule_add(ctx,0x50000|errno.EPERM,number,0)!=0:raise OSError('seccomp rule rejected')
  if sec.seccomp_load(ctx)!=0:raise OSError('seccomp load rejected')
 finally:sec.seccomp_release(ctx)

if __name__=='__main__':
 args=sys.argv[1:]
 if not args or args[0] not in ('/usr/bin/pdfinfo','/usr/bin/pdftotext'):raise SystemExit(2)
 for kind,limit in [(resource.RLIMIT_AS,512*1024*1024),(resource.RLIMIT_CPU,25),(resource.RLIMIT_FSIZE,16*1024*1024),(resource.RLIMIT_NOFILE,32),(resource.RLIMIT_CORE,0)]:resource.setrlimit(kind,(limit,limit))
 try:
  if ctypes.CDLL(None).prctl(38,1,0,0,0)!=0:raise OSError('no_new_privs rejected')
  # The adapter supplies only one verified input PDF. stdout/stderr are already-open bounded files.
  original=args[-2] if args[0].endswith('pdftotext') else args[-1]
  restrict_syscalls();restrict_filesystem(original)
 except OSError:
  sys.stderr.write('HUB_PDF_SANDBOX_UNAVAILABLE\n');raise SystemExit(78)
 os.execve(args[0],args,os.environ)
