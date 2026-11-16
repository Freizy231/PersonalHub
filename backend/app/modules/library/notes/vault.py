"""Private adapter for the Note domain. No ordinary DB or plaintext persistence."""
from copy import deepcopy
import fcntl
import json
import math
import os
import secrets
import stat
import threading
import time
from uuid import uuid4

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from ....core.database import HubError, utc_now
from ....core.note_files import digest, directory
from ....core.vault_files import VaultFiles, MAX_VAULT_BYTES

MAGIC = b'PHVAULT1'
IDLE_SECONDS = 300
ABSOLUTE_SECONDS = 1800


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def derive(password, salt):
    return Scrypt(salt=salt, length=32, n=2**17, r=8, p=1).derive(password.encode('utf-8'))


def seal(data, key, salt):
    plain = encoded(data)
    if len(plain) + 52 > MAX_VAULT_BYTES: raise HubError('VAULT_CAPACITY', 409)
    header = MAGIC + salt + secrets.token_bytes(12)
    return header + AESGCM(key).encrypt(header[24:36], plain, header)


def unseal(raw, password):
    if len(raw) < 52 or raw[:8] != MAGIC: raise HubError('VAULT_UNLOCK_FAILED', 403)
    key = derive(password, raw[8:24])
    try:
        plain = AESGCM(key).decrypt(raw[24:36], raw[36:], raw[:36])
        data = json.loads(plain)
        if set(data) != {'schema_version', 'notes', 'operations'} or data['schema_version'] != 1:
            raise ValueError
        if not isinstance(data['notes'], dict) or not isinstance(data['operations'], dict): raise ValueError
    except (InvalidTag, ValueError, TypeError, KeyError, UnicodeError):
        raise HubError('VAULT_UNLOCK_FAILED', 403) from None
    return data, key


class VaultNoteStore:
    def __init__(self, root, *, clock=time.monotonic):
        self.files = VaultFiles(root)
        self.clock = clock
        self.mutex = threading.RLock()
        self.key = self.data = self.owner = self.expected = self.salt = None
        self.idle_until = self.absolute_until = 0
        self.lease = None
        self.failures = 0
        self.retry_at = 0

    def _lease(self):
        with directory(self.files.root / 'config') as fd:
            descriptor = os.open('vault-session.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600, dir_fd=fd)
            try:
                info = os.fstat(descriptor)
                if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077: raise HubError('VAULT_FILE_UNSAFE', 409)
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                os.close(descriptor); raise HubError('VAULT_BUSY', 409) from None
            except BaseException:
                os.close(descriptor); raise
        self.lease = descriptor

    def lock(self, owner=None):
        with self.mutex:
            if owner is not None and self.owner is not None and owner != self.owner: return
            self.key = self.data = self.owner = self.expected = self.salt = None
            self.idle_until = self.absolute_until = 0
            if self.lease is not None:
                os.close(self.lease); self.lease = None

    def expire(self, sessions=None):
        with self.mutex:
            now = self.clock()
            if self.owner is not None and (now >= min(self.idle_until, self.absolute_until) or
                (sessions is not None and not any(s.csrf_token == self.owner and s.expires_at > now for s in list(sessions.values())))):
                self.lock()

    def status(self, owner):
        with self.mutex:
            self.expire()
            try: initialized = self.files.initialized()
            except BaseException:
                self.lock(); raise
            active = self.owner == owner and self.key is not None
            return {'initialized': initialized, 'unlocked': active,
                    'expires_in': max(0, math.floor(min(self.idle_until, self.absolute_until) - self.clock())) if active else 0,
                    'idle_seconds': IDLE_SECONDS, 'absolute_seconds': ABSOLUTE_SECONDS}

    def _authorize(self, owner, *, touch=True):
        self.expire()
        if self.owner != owner or self.key is None: raise HubError('VAULT_LOCKED', 423)
        try: self.files.read(expected=self.expected)
        except BaseException:
            self.lock(); raise
        if touch: self.idle_until = min(self.clock() + IDLE_SECONDS, self.absolute_until)

    def open(self, owner, password, *, create=False, session_deadline=float("inf")):
        with self.mutex:
            self.expire()
            if self.key is not None:
                if self.owner != owner: raise HubError('VAULT_BUSY', 409)
                # Same browser session may explicitly reauthenticate another panel.
                # A fresh password is still verified; no silent shared unlock.
                self.lock()
            if self.clock() < self.retry_at: raise HubError('VAULT_RATE_LIMIT', 429)
            self._lease()
            try:
                exists = bool(self.files.inventory())
                if create:
                    if exists: raise HubError('VAULT_EXISTS', 409)
                    salt = secrets.token_bytes(16); key = derive(password, salt)
                    data = {'schema_version': 1, 'notes': {}, 'operations': {}}
                    raw = seal(data, key, salt)
                    self.files.write(raw, create=True)
                else:
                    raw = self.files.read(); data, key = unseal(raw, password); salt = raw[8:24]
                self.key, self.data, self.salt, self.owner, self.expected = key, data, salt, owner, digest(raw)
                self.idle_until = self.clock() + IDLE_SECONDS
                self.absolute_until = min(self.clock() + ABSOLUTE_SECONDS, session_deadline)
                self.failures = 0; self.retry_at = 0
                return self.status(owner)
            except BaseException as error:
                self.lock()
                if isinstance(error, HubError) and error.code == 'VAULT_UNLOCK_FAILED':
                    self.failures += 1; self.retry_at = self.clock() + min(30, 2**min(self.failures, 5))
                raise

    def touch(self, owner):
        with self.mutex:
            self._authorize(owner)
            return self.status(owner)

    def change_password(self, owner, old_password, new_password):
        with self.mutex:
            self._authorize(owner)
            unseal(self.files.read(expected=self.expected), old_password)
            salt = secrets.token_bytes(16); key = derive(new_password, salt)
            raw = seal(self.data, key, salt)
            self._authorize(owner, touch=False)
            try: self.files.write(raw)
            except BaseException:
                self.lock(); raise
            self.lock()
            return {'status': 'locked'}

    def list(self, owner, trash=False, limit=20, offset=0):
        with self.mutex:
            self._authorize(owner)
            values = [n for n in self.data['notes'].values() if bool(n['deleted_at']) == trash]
            values.sort(key=lambda n:(n['updated_at'], n['id']), reverse=True)
            return {'items': [{k:v for k,v in n.items() if k not in ('body','revisions')} for n in values[offset:offset+limit]], 'total': len(values)}

    def get(self, owner, rid):
        with self.mutex:
            self._authorize(owner)
            note = self.data['notes'].get(rid)
            if note is None: raise HubError('NOTE_NOT_FOUND', 404)
            return deepcopy(note)

    def save(self, owner, payload, operation, rid=None, action='save'):
        with self.mutex:
            self._authorize(owner)
            fingerprint = digest(encoded({'rid': rid, 'action': action, 'payload': payload}))
            previous = self.data['operations'].get(operation)
            if previous:
                if previous['hash'] != fingerprint: raise HubError('IDEMPOTENCY_CONFLICT', 409)
                return self.get(owner, previous['rid'])
            if len(self.data['operations']) >= 4096: raise HubError('VAULT_CAPACITY', 409)
            data = deepcopy(self.data)
            now = utc_now()
            if rid is None:
                if len(data['notes']) >= 1000: raise HubError('VAULT_CAPACITY', 409)
                rid = str(uuid4())
                note = {'id': rid, 'version': 0, 'created_at': now, 'deleted_at': None, 'revisions': []}
            else:
                note = data['notes'].get(rid)
                if note is None: raise HubError('NOTE_NOT_FOUND', 404)
                if payload['expected_version'] != note['version']: raise HubError('VERSION_CONFLICT', 409)
                if action == 'save' and note['deleted_at']: raise HubError('NOTE_STATE_CONFLICT', 409)
            note['version'] += 1; note['updated_at'] = now
            if action == 'save':
                for name in ('title', 'body', 'note_kind', 'local_date'): note[name] = payload[name]
                note['revisions'].append({k: deepcopy(note[k]) for k in ('title','body','note_kind','local_date','version','updated_at')})
            else:
                if bool(note['deleted_at']) != (action == 'restore'): raise HubError('NOTE_STATE_CONFLICT', 409)
                note['deleted_at'] = now if action == 'trash' else None
            data['notes'][rid] = note
            data['operations'][operation] = {'hash': fingerprint, 'rid': rid}
            raw = seal(data, self.key, self.salt)
            self._authorize(owner, touch=False)
            try: self.files.write(raw)
            except BaseException:
                self.lock(); raise
            self.data, self.expected = data, digest(raw)
            return deepcopy(note)
