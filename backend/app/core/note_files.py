"""Local immutable Markdown storage. No arbitrary client paths, no overwrites."""
from contextlib import contextmanager
import fcntl
import hashlib
import os
from pathlib import Path,PurePosixPath
import stat
from .database import HubError

MAX_BYTES=1024*1024


def digest(data):return hashlib.sha256(data).hexdigest()


def parts(relative):
    if not isinstance(relative,str) or '\\' in relative or '\x00' in relative:
        raise HubError('NOTE_PATH_UNSAFE',409)
    parsed=PurePosixPath(relative)
    if parsed.is_absolute() or not parsed.parts or any(p in ('','.','..') for p in relative.split('/')) or parsed.parts[0]!='notes':
        raise HubError('NOTE_PATH_UNSAFE',409)
    return parsed.parts


@contextmanager
def directory(path,*,create=False):
    """Traverse using directory descriptors; refuse every symlink component."""
    path=Path(path).absolute()
    fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY)
    try:
        try:
            for name in path.parts[1:]:
                if create:
                    try:os.mkdir(name,0o700,dir_fd=fd);os.fsync(fd)
                    except FileExistsError:pass
                child=os.open(name,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
                os.close(fd);fd=child
        except FileNotFoundError:raise
        except OSError:raise HubError('NOTE_PATH_UNSAFE',409) from None
        yield fd
    finally:os.close(fd)


@contextmanager
def file_lock(root):
    root=Path(root)
    with directory(root) as root_fd:
        if os.fstat(root_fd).st_mode & 0o077:raise HubError('PRIVATE_PATH_REQUIRED',503)
    with directory(root/'config') as config_fd:
        fd=os.open('notes-files.lock',os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600,dir_fd=config_fd)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode) or os.fstat(fd).st_mode & 0o077:
                raise HubError('PRIVATE_PATH_REQUIRED',503)
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise HubError('NOTE_FILES_BUSY',409) from None
            yield
        finally:os.close(fd)


class NoteFiles:
    def __init__(self,root):self.root=Path(root)

    @contextmanager
    def folder(self,components,create=False):
        target=self.root
        for component in [None,*components]:
            if component is not None:target=target/component
            with directory(target,create=create) as fd:
                if os.fstat(fd).st_mode & 0o077:raise HubError('NOTE_FILE_UNSAFE',409)
        with directory(target) as fd:yield fd

    def read(self,relative,expected=None):
        components=parts(relative)
        try:
            with self.folder(components[:-1]) as fd:
                file_fd=os.open(components[-1],os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
                with os.fdopen(file_fd,'rb') as handle:
                    info=os.fstat(handle.fileno())
                    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_size>MAX_BYTES:
                        raise HubError('NOTE_FILE_UNSAFE',409)
                    data=handle.read(MAX_BYTES+1)
            if len(data)>MAX_BYTES:raise HubError('NOTE_FILE_UNSAFE',409)
        except FileNotFoundError:raise HubError('NOTE_FILE_MISSING',409) from None
        except OSError:raise HubError('NOTE_FILE_UNSAFE',409) from None
        if expected is not None and digest(data)!=expected:raise HubError('NOTE_FILE_CONFLICT',409)
        return data

    def body(self,relative,expected):
        try:return self.read(relative,expected).decode('utf-8')
        except UnicodeDecodeError:raise HubError('NOTE_FILE_CONFLICT',409) from None

    def write_revision(self,relative,temp_path,data):
        components=parts(relative);temporary=parts(temp_path)
        if components[:-1]!=temporary[:-1]:raise HubError('NOTE_PATH_UNSAFE',409)
        with self.folder(components[:-1],create=True) as fd:
            if os.fstat(fd).st_mode & 0o077:raise HubError('PRIVATE_PATH_REQUIRED',503)
            descriptor=os.open(temporary[-1],os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd)
            with os.fdopen(descriptor,'wb') as handle:
                handle.write(data);handle.flush();os.fsync(handle.fileno())
            # Atomic creation, refuses any existing path, including a symlink.
            os.link(temporary[-1],components[-1],src_dir_fd=fd,dst_dir_fd=fd,follow_symlinks=False)
            os.fsync(fd)
            os.unlink(temporary[-1],dir_fd=fd)
            os.fsync(fd)
        self.read(relative,digest(data))

    def unlink(self,relative,parts_fn=parts):
        """Remove one regular file without ever following a symlink.

        Missing file is success: the caller's row is being removed either way.
        """
        components=parts_fn(relative)
        try:
            with self.folder(components[:-1]) as fd:
                info=os.stat(components[-1],dir_fd=fd,follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode):raise HubError('NOTE_FILE_UNSAFE',409)
                os.unlink(components[-1],dir_fd=fd)
                os.fsync(fd)
        except FileNotFoundError:return False
        except OSError:raise HubError('NOTE_FILE_UNSAFE',409) from None
        return True

    def inventory(self):
        """Include unknown/temp files without following symlinks; preserve rather than clean."""
        result={}
        if not (self.root/'notes').exists() and not (self.root/'notes').is_symlink():return result
        def walk(relative):
            with self.folder(parts(relative)) as fd:
                for name in sorted(os.listdir(fd)):
                    info=os.stat(name,dir_fd=fd,follow_symlinks=False)
                    child=relative+'/'+name
                    if stat.S_ISDIR(info.st_mode):walk(child)
                    elif stat.S_ISREG(info.st_mode):
                        data=self.read(child);result[child]={'sha256':digest(data),'size':len(data)}
                    else:raise HubError('NOTE_FILE_UNSAFE',409)
        walk('notes')
        return result
