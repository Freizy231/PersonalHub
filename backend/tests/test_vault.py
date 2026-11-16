"""Real Scrypt/AESGCM, isolated fictitious data; never initializes the user's vault."""
import asyncio
from contextlib import closing
from copy import deepcopy
import json
import os
import subprocess
import sys
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

import httpx
from backend.tests.async_support import AsyncAPITestCase
from backend.app.core.config import Settings
from backend.app.core.database import HubError
from backend.app.core.vault_files import VaultFiles
from backend.app.core.note_snapshot import verified_snapshot
from backend.app.modules.library.notes.vault import VaultNoteStore, derive, seal, unseal
from backend.app.main import create_app
from backend.manage import initialize_empty
from backend.maintenance import upgrade, backup_hub, restore_hub
from scripts.data_safety import audit_database

PASSWORD = 'fictitious-test-only-password'
NEW_PASSWORD = 'another-fictitious-password'
SENTINEL = '虚构PRIVATE-SENTINEL-93812'


def key(): return str(uuid4())
def payload(**kw): return {'title': SENTINEL+'标题', 'body': SENTINEL+'正文', 'note_kind':'private_journal', 'local_date':'2026-11-12', **kw}


class VaultFacts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.root = Path(self.temp.name)
        (self.root/'config').mkdir(mode=0o700)
        self.now = 0
        self.store = VaultNoteStore(self.root, clock=lambda:self.now)
    def tearDown(self):
        self.store.lock();self.temp.cleanup()
    def setup_vault(self): self.store.open('owner', PASSWORD, create=True)
    def assert_code(self, code, call):
        with self.assertRaises(HubError) as caught: call()
        self.assertEqual(caught.exception.code, code)

    def test_ciphertext_randomization_and_metadata_including_history_never_persist_plain(self):
        self.setup_vault(); row=self.store.save('owner',payload(),key())
        self.store.save('owner',payload(title='第二标题',expected_version=1),key(),row['id'])
        raw=self.store.files.read()
        self.assertNotIn(SENTINEL.encode(),raw);self.assertNotIn(b'2026-11-12',raw)
        data,k=unseal(raw,PASSWORD)
        first=seal(data,k,raw[8:24]);second=seal(data,k,raw[8:24]);self.assertNotEqual(first,second)
        self.assertEqual(unseal(first,PASSWORD)[0],data)
        self.assertEqual(len(data['notes'][row['id']]['revisions']),2)
        for path in self.root.rglob('*'):
            if path.is_file():self.assertNotIn(SENTINEL.encode(),path.read_bytes())
        self.assertEqual((self.root/'vault'/'vault.phv').stat().st_mode&0o777,0o600)
        self.assertEqual((self.root/'vault').stat().st_mode&0o777,0o700)

    def test_bad_password_tamper_truncation_header_all_refuse(self):
        self.setup_vault();raw=self.store.files.read();self.store.lock()
        variants=[b'',raw[:20],raw[:-1],raw[:36]+bytes([raw[36]^1])+raw[37:],b'X'+raw[1:],raw[:10]+bytes([raw[10]^1])+raw[11:]]
        for variant in variants:
            self.assert_code('VAULT_UNLOCK_FAILED',lambda:unseal(variant,PASSWORD))
        self.assert_code('VAULT_UNLOCK_FAILED',lambda:self.store.open('owner',NEW_PASSWORD))
        self.assert_code('VAULT_RATE_LIMIT',lambda:self.store.open('owner',PASSWORD))
        self.now=31;self.store.open('owner',PASSWORD)

    def test_owner_idle_absolute_restart_and_status_does_not_extend(self):
        self.setup_vault();self.assertFalse(self.store.status('other')['unlocked'])
        self.assert_code('VAULT_LOCKED',lambda:self.store.list('other'))
        self.now=299;self.assertTrue(self.store.status('owner')['unlocked'])
        self.now=300;self.assertFalse(self.store.status('owner')['unlocked']);self.assertIsNone(self.store.key)
        self.store.open('owner',PASSWORD)
        for now in range(500,2100,200):self.now=now;self.store.touch('owner')
        self.now=2100;self.assert_code('VAULT_LOCKED',lambda:self.store.get('owner',key()))
        fresh=VaultNoteStore(self.root);self.assertFalse(fresh.status('owner')['unlocked'])

    def test_session_deadline_and_revocation_lock(self):
        self.store.open('owner',PASSWORD,create=True,session_deadline=20)
        self.now=20;self.assertFalse(self.store.status('owner')['unlocked'])
        self.store.open('owner',PASSWORD);self.store.expire({});self.assertIsNone(self.store.key)

    def test_second_process_adapter_lease_and_external_replacement_cannot_overwrite(self):
        self.setup_vault();self.store.open('owner',PASSWORD);other=VaultNoteStore(self.root)
        self.assert_code('VAULT_BUSY',lambda:other.open('other',PASSWORD))
        script = """
import sys
from backend.app.modules.library.notes.vault import VaultNoteStore
from backend.app.core.database import HubError
try:VaultNoteStore(sys.argv[1]).open('other','fictitious-test-only-password')
except HubError as e:
 print(e.code);sys.exit(0 if e.code=='VAULT_BUSY' else 2)
sys.exit(3)
"""
        result=subprocess.run([sys.executable,'-B','-c',script,str(self.root)],capture_output=True,timeout=10)
        self.assertEqual(result.returncode,0);self.assertEqual(result.stdout.strip(),b'VAULT_BUSY')
        self.store.lock();other.open('other',PASSWORD);other.lock();self.store.open('owner',PASSWORD)
        path=self.root/'vault'/'vault.phv';original=path.read_bytes();path.write_bytes(original[:-1]+b'X')
        self.assert_code('VAULT_CHANGED',lambda:self.store.save('owner',payload(),key()))
        self.assertIsNone(self.store.key);self.assertEqual(path.read_bytes(),original[:-1]+b'X')

    def test_revision_conflict_idempotency_trash_restore_and_reunlock(self):
        self.setup_vault();operation=key();row=self.store.save('owner',payload(),operation)
        self.assertEqual(self.store.save('owner',payload(),operation),row)
        self.assert_code('IDEMPOTENCY_CONFLICT',lambda:self.store.save('owner',payload(body='different'),operation))
        edit=payload(body='new body',expected_version=1);edit_key=key()
        updated=self.store.save('owner',edit,edit_key,row['id'])
        self.assertEqual(self.store.save('owner',edit,edit_key,row['id']),updated)
        self.assert_code('VERSION_CONFLICT',lambda:self.store.save('owner',edit,key(),row['id']))
        deleted=self.store.save('owner',{'expected_version':2},key(),row['id'],'trash')
        self.assertEqual(self.store.list('owner')['total'],0);self.assertEqual(self.store.list('owner',True)['total'],1)
        restored=self.store.save('owner',{'expected_version':3},key(),row['id'],'restore')
        self.assertEqual(restored['version'],4);self.assertEqual(len(restored['revisions']),2)
        self.store.lock();self.store.open('owner',PASSWORD)
        self.assertEqual(self.store.get('owner',row['id']),restored)

    def test_pre_replace_failure_keeps_old_password_and_data_then_atomic_change_locks(self):
        self.setup_vault();row=self.store.save('owner',payload(),key());before=self.store.files.read()
        with patch('backend.app.core.vault_files.os.replace',side_effect=OSError('fictitious disk error')):
            with self.assertRaises(OSError):self.store.change_password('owner',PASSWORD,NEW_PASSWORD)
        self.assertEqual(self.store.files.read(),before);self.assertIsNone(self.store.key)
        self.store.open('owner',PASSWORD);self.assertEqual(self.store.get('owner',row['id'])['body'],payload()['body'])
        self.store.change_password('owner',PASSWORD,NEW_PASSWORD);self.assertIsNone(self.store.key)
        raw=self.store.files.read();self.assert_code('VAULT_UNLOCK_FAILED',lambda:unseal(raw,PASSWORD))
        self.store.open('owner',NEW_PASSWORD);self.assertEqual(self.store.list('owner')['total'],1)
        self.assertGreater(len(self.store.files.inventory()),1) # ciphertext recovery temp preserved

    def test_post_replace_fsync_failure_locks_and_retry_after_reunlock_is_idempotent(self):
        self.setup_vault();operation=key();original=os.fsync;calls=0
        def failing(fd):
            nonlocal calls
            calls+=1
            if calls==2:raise OSError('directory sync failed')
            return original(fd)
        with patch('backend.app.core.vault_files.os.fsync',side_effect=failing):
            with self.assertRaises(OSError):self.store.save('owner',payload(),operation)
        self.assertIsNone(self.store.data);self.store.open('owner',PASSWORD)
        row=self.store.save('owner',payload(),operation)
        self.assertEqual(self.store.list('owner')['total'],1);self.assertEqual(len(row['revisions']),1)

    def test_setup_refuses_existing_and_orphan_only_tree(self):
        self.setup_vault();before=self.store.files.read();self.store.lock()
        self.assert_code('VAULT_EXISTS',lambda:self.store.open('owner',NEW_PASSWORD,create=True))
        self.assertEqual(self.store.files.read(),before)
        (self.root/'vault'/'vault.phv').rename(self.root/'vault'/'recovery.tmp')
        self.assert_code('VAULT_RECOVERY_REQUIRED',lambda:self.store.open('owner',PASSWORD,create=True))

    def test_symlink_public_permission_and_fifo_refused(self):
        self.setup_vault();self.store.lock();path=self.root/'vault'/'vault.phv';path.chmod(0o644)
        self.assert_code('VAULT_FILE_UNSAFE',lambda:self.store.open('owner',PASSWORD))
        path.chmod(0o600);path.rename(path.with_suffix('.old'));path.symlink_to(path.with_suffix('.old'))
        self.assert_code('VAULT_FILE_UNSAFE',lambda:self.store.files.read())
        path.unlink();os.mkfifo(path,0o600)
        self.assert_code('VAULT_FILE_UNSAFE',lambda:self.store.files.read())

    def test_actual_process_exit_before_replace_preserves_old_container_and_only_ciphertext_temps(self):
        self.setup_vault();row=self.store.save('owner',payload(),key());before=self.store.files.read();self.store.lock()
        script = """
import os,sys
from unittest.mock import patch
from backend.app.modules.library.notes.vault import VaultNoteStore
store=VaultNoteStore(sys.argv[1]);store.open('owner','fictitious-test-only-password')
with patch('backend.app.core.vault_files.os.replace',side_effect=lambda *a,**k:os._exit(23)):
 store.save('owner',{'title':'PRIVATE-crash-title','body':'PRIVATE-crash-body','note_kind':'treehole','local_date':None},'crash-operation')
"""
        result=subprocess.run([sys.executable,'-B','-c',script,str(self.root)],capture_output=True,timeout=15)
        self.assertEqual(result.returncode,23,result.stderr.decode())
        self.assertEqual(self.store.files.read(),before)
        self.store.open('owner',PASSWORD);self.assertEqual(self.store.get('owner',row['id'])['version'],1)
        for path in (self.root/'vault').iterdir():self.assertNotIn(b'PRIVATE-crash',path.read_bytes())
        self.assertGreater(len(self.store.files.inventory()),1)

    def test_expiry_during_encryption_prevents_late_commit(self):
        self.setup_vault();before=self.store.files.read()
        from backend.app.modules.library.notes import vault
        original=vault.seal
        def expire(*args):
            result=original(*args);self.now=301;return result
        with patch.object(vault,'seal',side_effect=expire):
            self.assert_code('VAULT_LOCKED',lambda:self.store.save('owner',payload(),key()))
        self.assertEqual(self.store.files.read(),before);self.assertIsNone(self.store.key)

    def test_capacity_preserves_existing_data(self):
        self.setup_vault();before=self.store.files.read()
        with patch('backend.app.modules.library.notes.vault.MAX_VAULT_BYTES',60):
            self.assert_code('VAULT_CAPACITY',lambda:self.store.save('owner',payload(),key()))
        self.assertEqual(self.store.files.read(),before);self.assertEqual(self.store.list('owner')['total'],0)


class VaultBackup(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.settings=Settings(data_root=self.root/'hub',legacy_database=self.root/'legacy',reminders_enabled=False)
        initialize_empty(self.settings.data_root);upgrade(self.settings)
        self.store=VaultNoteStore(self.settings.data_root)
    def tearDown(self):self.store.lock();self.temp.cleanup()
    def test_complete_encrypted_snapshot_restore_and_plain_db_unchanged(self):
        before=audit_database(self.settings.database)
        self.store.open('owner',PASSWORD,create=True);row=self.store.save('owner',payload(),key());self.store.lock()
        vault=VaultFiles(self.settings.data_root);(self.settings.data_root/'vault'/'recovery.tmp').write_bytes(vault.read())
        (self.settings.data_root/'vault'/'recovery.tmp').chmod(0o600)
        self.assertEqual(audit_database(self.settings.database),before)
        backup=self.root/'backup';output=self.root/'restored';backup_hub(self.settings,backup)
        _,_,metadata=verified_snapshot(backup);self.assertEqual(metadata['format_version'],3)
        self.assertEqual(metadata['files'],vault.inventory())
        restore_hub(self.settings,backup,output)
        restored=VaultNoteStore(output);self.assertFalse(restored.status('owner')['unlocked'])
        restored.open('owner',PASSWORD);self.assertEqual(restored.get('owner',row['id'])['body'],payload()['body']);restored.lock()
        self.assertEqual(audit_database(output/'db'/'hub.db'),before)
        for file in output.rglob('*'):
            if file.is_file():self.assertNotIn(SENTINEL.encode(),file.read_bytes())
        path=backup/'vault'/'vault.phv';path.write_bytes(path.read_bytes()[:-1])
        with self.assertRaises(HubError):verified_snapshot(backup)

    def test_v1_compatibility_but_vault_not_silently_omitted(self):
        backup=self.root/'backup'
        from backend.app.core.database import connection
        from backend.app.core.config import PROJECT_ROOT
        from alembic.config import Config
        from alembic import command
        legacy=Settings(data_root=self.root/'older',legacy_database=self.root/'legacy')
        initialize_empty(legacy.data_root)
        with connection(legacy.database,write=True,require_current=False,enforce_foreign_keys=False) as con:
            config=Config(str(PROJECT_ROOT/'backend/alembic.ini'));config.attributes['connection']=con
            command.upgrade(config,'0005_ordinary_tags')
        backup_hub(legacy,backup)
        path=backup/'hub-files.json';manifest=json.loads(path.read_text());manifest['format_version']=1;path.write_text(json.dumps(manifest))
        verified_snapshot(backup);restore_hub(self.settings,backup,self.root/'restored')
        store=VaultNoteStore(backup);(backup/'config').mkdir(mode=0o700);store.open('owner',PASSWORD,create=True);store.lock()
        with self.assertRaises(HubError):verified_snapshot(backup)


class VaultAPI(AsyncAPITestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.settings=Settings(data_root=self.root/'hub',legacy_database=self.root/'legacy',reminders_enabled=False)
        initialize_empty(self.settings.data_root);upgrade(self.settings)
        self.app=create_app(self.settings);self.origin='http://127.0.0.1:8765'
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url=self.origin)
        result=await self.client.post('/api/v1/session/exchange',json={'token':self.app.state.auth.bootstrap},headers={'Origin':self.origin})
        self.headers={'Origin':self.origin,'X-CSRF-Token':result.json()['csrf_token']}
    async def asyncTearDown(self):
        self.app.state.vault.lock();await self.client.aclose();self.temp.cleanup()
    async def setup_vault(self):
        r=await self.client.post('/api/v1/vault/setup',json={'password':PASSWORD,'confirmation':PASSWORD,'acknowledge_no_recovery':True},headers=self.headers)
        self.assertEqual(r.status_code,200,r.text)

    async def test_auth_csrf_origin_validation_and_no_input_echo(self):
        for data in [{'password':PASSWORD,'confirmation':'mismatch-password','acknowledge_no_recovery':True},{'password':'short'}, {'password':'\ud800'*12}, {'password':PASSWORD,'extra':SENTINEL}]:
            r=await self.client.post('/api/v1/vault/setup',content=json.dumps(data),headers=self.headers)
            self.assertEqual(r.status_code,422,r.text);self.assertNotIn(PASSWORD,r.text);self.assertNotIn(SENTINEL,r.text)
        self.assertEqual((await self.client.post('/api/v1/vault/unlock',json={'password':PASSWORD},headers={'Origin':self.origin})).status_code,403)
        self.assertEqual((await self.client.post('/api/v1/vault/unlock',json={'password':PASSWORD},headers={**self.headers,'Origin':'https://hostile.invalid'})).status_code,403)
        self.client.cookies.clear();self.assertEqual((await self.client.get('/api/v1/vault/status')).status_code,401)

    async def test_private_crud_locked_routes_and_ordinary_exports_never_leak(self):
        before=audit_database(self.settings.database);await self.setup_vault()
        r=await self.client.post('/api/v1/vault/notes',json=payload(),headers={**self.headers,'Idempotency-Key':key()})
        self.assertEqual(r.status_code,200,r.text);row=r.json()
        r=await self.client.put('/api/v1/vault/notes/'+row['id'],json=payload(body='new',expected_version=1),headers={**self.headers,'Idempotency-Key':key()})
        self.assertEqual(r.status_code,200,r.text)
        self.assertEqual(len((await self.client.get('/api/v1/vault/notes/'+row['id'])).json()['revisions']),2)
        self.assertEqual((await self.client.get('/api/v1/notes')).json()['total'],0)
        for route in ['/api/v1/notes/export','/api/v1/tasks/export','/api/v1/events/export']:
            response=await self.client.get(route);self.assertNotIn(SENTINEL.encode(),response.content)
        self.assertEqual(audit_database(self.settings.database),before)
        await self.client.post('/api/v1/vault/lock',headers=self.headers)
        for route in ['/api/v1/vault/notes','/api/v1/vault/notes/'+row['id']]:
            response=await self.client.get(route);self.assertEqual(response.status_code,423);self.assertNotIn(SENTINEL,response.text)
        self.assertNotIn(row['id'],(await self.client.get('/api/v1/vault/status')).text)
        response=await self.client.post('/api/v1/vault/unlock',json={'password':PASSWORD},headers=self.headers);self.assertEqual(response.status_code,200,response.text)
        await self.client.post('/api/v1/session/logout',headers=self.headers);self.assertIsNone(self.app.state.vault.key)

    async def test_lifespan_reaper_locks_revoked_session_without_a_vault_request(self):
        async with self.app.router.lifespan_context(self.app):
            await self.setup_vault()
            self.app.state.auth.sessions.clear()
            await asyncio.sleep(1.15)
            self.assertIsNone(self.app.state.vault.key)
            self.assertIsNone(self.app.state.vault.data)

    async def test_incomplete_restored_root_cannot_initialize_vault(self):
        (self.settings.data_root/'HUB_INCOMPLETE.json').write_text('{}')
        result=await self.client.post('/api/v1/vault/setup',json={'password':PASSWORD,'confirmation':PASSWORD,'acknowledge_no_recovery':True},headers=self.headers)
        self.assertEqual(result.status_code,503)
        self.assertFalse((self.settings.data_root/'vault').exists())

    async def test_parallel_creation_retry_one_fact_and_request_size_limit(self):
        await self.setup_vault();headers={**self.headers,'Idempotency-Key':key()}
        responses=await asyncio.gather(*[self.client.post('/api/v1/vault/notes',json=payload(),headers=headers) for _ in range(3)])
        self.assertTrue(all(r.status_code==200 for r in responses));self.assertEqual(len({r.json()['id'] for r in responses}),1)
        self.assertEqual((await self.client.get('/api/v1/vault/notes')).json()['total'],1)
        response=await self.client.post('/api/v1/vault/notes',content=b'x'*(1024*1024+1),headers=headers)
        self.assertEqual(response.status_code,413)
