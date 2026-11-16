"""Ciphertext-only vault storage, also used by stopped-writer snapshots."""
from pathlib import Path, PurePosixPath
import os
import secrets
import stat
from .database import HubError
from .note_files import directory, digest

MAX_VAULT_BYTES = 16 * 1024 * 1024


def vault_parts(relative):
    if not isinstance(relative, str) or '\\' in relative or '\x00' in relative:
        raise HubError('VAULT_FILE_UNSAFE', 409)
    parsed = PurePosixPath(relative)
    if parsed.is_absolute() or len(parsed.parts) != 2 or parsed.parts[0] != 'vault' or any(x in ('', '.', '..') for x in relative.split('/')):
        raise HubError('VAULT_FILE_UNSAFE', 409)
    return parsed.parts


class VaultFiles:
    def __init__(self, root):
        self.root = Path(root)

    def folder(self, create=False):
        with directory(self.root) as fd:
            if os.fstat(fd).st_mode & 0o077:
                raise HubError('VAULT_FILE_UNSAFE', 409)
        return directory(self.root / 'vault', create=create)

    def read(self, relative='vault/vault.phv', expected=None):
        _, name = vault_parts(relative)
        try:
            with self.folder() as fd:
                if os.fstat(fd).st_mode & 0o077: raise HubError('VAULT_FILE_UNSAFE', 409)
                descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
                with os.fdopen(descriptor, 'rb') as handle:
                    info = os.fstat(handle.fileno())
                    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_size > MAX_VAULT_BYTES:
                        raise HubError('VAULT_FILE_UNSAFE', 409)
                    data = handle.read(MAX_VAULT_BYTES + 1)
            if len(data) > MAX_VAULT_BYTES: raise HubError('VAULT_CAPACITY', 409)
            if expected is not None and digest(data) != expected: raise HubError('VAULT_CHANGED', 409)
            return data
        except FileNotFoundError: raise HubError('VAULT_NOT_INITIALIZED', 409) from None
        except OSError: raise HubError('VAULT_FILE_UNSAFE', 409) from None

    def initialized(self):
        path = self.root / 'vault'
        if not path.exists() and not path.is_symlink(): return False
        with self.folder() as fd:
            if os.fstat(fd).st_mode & 0o077: raise HubError('VAULT_FILE_UNSAFE', 409)
            try: info = os.stat('vault.phv', dir_fd=fd, follow_symlinks=False)
            except FileNotFoundError:
                if os.listdir(fd): raise HubError('VAULT_RECOVERY_REQUIRED', 409)
                return False
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_size > MAX_VAULT_BYTES:
                raise HubError('VAULT_FILE_UNSAFE', 409)
        return True

    def inventory(self):
        path = self.root / 'vault'
        if not path.exists() and not path.is_symlink(): return {}
        result = {}
        with self.folder() as fd:
            for name in sorted(os.listdir(fd)):
                relative = 'vault/' + name
                data = self.read(relative)
                result[relative] = {'sha256': digest(data), 'size': len(data)}
        # A nonempty recovery tree without its active container must not look like a new vault.
        if result and 'vault/vault.phv' not in result: raise HubError('VAULT_RECOVERY_REQUIRED', 409)
        return result

    def write(self, data, *, create=False):
        if len(data) > MAX_VAULT_BYTES: raise HubError('VAULT_CAPACITY', 409)
        with self.folder(create=True) as fd:
            if os.fstat(fd).st_mode & 0o077: raise HubError('VAULT_FILE_UNSAFE', 409)
            temporary = secrets.token_hex(16) + '.tmp'
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
            with os.fdopen(descriptor, 'wb') as handle:
                handle.write(data); handle.flush(); os.fsync(handle.fileno())
            if create:
                os.link(temporary, 'vault.phv', src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
                os.unlink(temporary, dir_fd=fd)
            else:
                os.replace(temporary, 'vault.phv', src_dir_fd=fd, dst_dir_fd=fd)
            os.fsync(fd)
