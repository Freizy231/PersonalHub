"""Stopped-writer Hub snapshot: SQLite plus all managed Markdown/recovery files."""
from pathlib import Path
import json
import os
from sqlalchemy import text
from .database import connection,revision,HubError
from .note_files import NoteFiles,directory,digest,parts
from .vault_files import VaultFiles,vault_parts
from ..modules.library.documents.storage import BlobFiles,blob_parts
from scripts.data_safety import (create_backup,verified_backup,restore_isolated,write_json,
    sync_directory,DATABASE_NAME,copy_database,open_readonly,audit_connection,audit_database,matching_report)
from contextlib import closing

FILE_MANIFEST='hub-files.json'


def managed_parts(relative):
    return blob_parts(relative) if relative.startswith('library/') else vault_parts(relative) if relative.startswith('vault/') else parts(relative)

class SnapshotFiles:
    def __init__(self,root):self.root=Path(root)
    def read(self,relative,expected=None):
        store=BlobFiles(self.root) if relative.startswith('library/') else VaultFiles(self.root) if relative.startswith('vault/') else NoteFiles(self.root)
        return store.read(relative,expected)
    def inventory(self):
        return {**NoteFiles(self.root).inventory(),**VaultFiles(self.root).inventory(),**BlobFiles(self.root).inventory()}


def unique_pairs(pairs):
    result={}
    for name,value in pairs:
        if name in result:raise ValueError('duplicate manifest key')
        result[name]=value
    return result


def check_references(database,root):
    """Verify ALL revisions, including deleted/private. Never returns contents."""
    with connection(database,require_current=False) as con:
        has_notes=con.exec_driver_sql("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='notes'").first()
        if not has_notes:return
        for row in con.execute(text('SELECT body_path,content_sha256 FROM note_revisions')):
            NoteFiles(root).body(row.body_path,row.content_sha256)
        for row in con.execute(text('SELECT body_path,content_sha256 FROM notes')):
            NoteFiles(root).body(row.body_path,row.content_sha256)
        mismatch=con.exec_driver_sql('''SELECT 1 FROM notes n LEFT JOIN note_revisions r ON
            n.resource_id=r.note_id AND n.body_path=r.body_path AND n.content_sha256=r.content_sha256
            WHERE r.id IS NULL LIMIT 1''').first()
        if mismatch:raise HubError('NOTE_REFERENCE_INVALID',409)
        if con.exec_driver_sql("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='blobs'").first():
            store=BlobFiles(root)
            for row in con.exec_driver_sql('SELECT relative_path,sha256,size FROM blobs'):
                if len(store.read(row[0],row[1]))!=row[2]:raise HubError('BLOB_REFERENCE_INVALID')
            for row in con.exec_driver_sql('SELECT extracted_text_path,text_sha256 FROM documents WHERE extracted_text_path IS NOT NULL'):
                store.read(*row)



def copy_file(source_store,destination,relative,expected):
    data=source_store.read(relative,expected['sha256'])
    if len(data)!=expected['size']:raise HubError('NOTE_SNAPSHOT_CHANGED',409)
    components=managed_parts(relative)
    with directory(Path(destination).joinpath(*components[:-1]),create=True) as fd:
        descriptor=os.open(components[-1],os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=fd)
        with os.fdopen(descriptor,'wb') as handle:
            handle.write(data);handle.flush();os.fsync(handle.fileno())
        os.fsync(fd)


def snapshot(database,root,output):
    root,output=Path(root),Path(output)
    if output.resolve()==root.resolve() or output.resolve().is_relative_to((root/'notes').resolve()) or output.resolve().is_relative_to((root/'vault').resolve()) or output.resolve().is_relative_to((root/'library').resolve()):
        raise HubError('UNSAFE_BACKUP_OUTPUT',409)
    ancestor=output.absolute().parent
    while not ancestor.exists() and not ancestor.is_symlink():ancestor=ancestor.parent
    with directory(ancestor):pass
    store=SnapshotFiles(root)
    before=store.inventory()
    check_references(database,root)
    # Keep original SQLite manifest intact, for diagnostic/backward compatibility.
    manifest=create_backup(database,output)
    for relative,metadata in before.items():copy_file(store,output,relative,metadata)
    check_references(output/DATABASE_NAME,output)
    if store.inventory()!=before:raise HubError('NOTE_SNAPSHOT_CHANGED',409)
    if SnapshotFiles(output).inventory()!=before:raise HubError('NOTE_SNAPSHOT_CHANGED',409)
    write_json(output/FILE_MANIFEST,{'format':'personal-hub-full-snapshot','format_version':3,
        'state':'verified','revision':revision(output/DATABASE_NAME),
        'database_sha256':manifest['database']['sha256'],'files':before,
        'includes':['hub_database','notes','revisions','unreferenced_note_files','encrypted_vault','vault_recovery_files','library_originals','library_staging','library_text'],
        'excludes':['legacy_intelligence','secrets','external_directories']})
    sync_directory(output)
    return manifest


def _requires_full_manifest(rev):
    """Revisions at or after 0007 have notes/vault/library trees that a v2 manifest misses."""
    if not isinstance(rev,str) or '_' not in rev:
        return False
    try:
        number=int(rev.split('_',1)[0])
    except ValueError:
        return False
    return number>=7


def verified_snapshot(backup):
    backup=Path(backup)
    # Descriptor traversal rejects symlink parents as well as leaf files.
    with directory(backup):pass
    database,manifest=verified_backup(backup)
    rev=revision(database)
    path=backup/FILE_MANIFEST
    if not path.exists() and not path.is_symlink():
        if rev not in {'0000_foundation','0001_tasks','0002_events_reminders'}:
            raise HubError('NOTE_SNAPSHOT_INCOMPLETE',409)
        if SnapshotFiles(backup).inventory():raise HubError('NOTE_SNAPSHOT_INCOMPLETE',409)
        return database,manifest,None
    if path.is_symlink() or not path.is_file() or path.stat().st_size>16*1024*1024:
        raise HubError('NOTE_SNAPSHOT_INVALID',409)
    try:
        metadata=json.loads(path.read_text(),object_pairs_hook=unique_pairs)
        if metadata['format']!='personal-hub-full-snapshot' or metadata['format_version'] not in (1,2,3) or metadata['state']!='verified':raise ValueError
        if metadata['revision']!=rev or metadata['database_sha256']!=manifest['database']['sha256']:raise ValueError
        # Every revision that carries managed files needs the full v3 manifest (notes, vault and
        # library). Compared by schema generation rather than a hardcoded list, so a new revision
        # cannot silently accept an older, incomplete manifest.
        if _requires_full_manifest(rev) and metadata['format_version']<3:raise ValueError
        files=metadata['files']
        if not isinstance(files,dict):raise ValueError
        for relative,info in files.items():
            managed_parts(relative)
            if metadata['format_version']==1 and relative.startswith('vault/'):raise ValueError
            if metadata['format_version']<3 and relative.startswith('library/'):raise ValueError
            if not isinstance(info,dict) or set(info)!={'sha256','size'}:raise ValueError
            if not isinstance(info['sha256'],str) or len(info['sha256'])!=64 or type(info['size']) is not int or info['size']<0:raise ValueError
        if SnapshotFiles(backup).inventory()!=files:raise ValueError
    except (KeyError,ValueError,TypeError):raise HubError('NOTE_SNAPSHOT_INVALID',409) from None
    check_references(database,backup)
    return database,manifest,metadata


def restore_snapshot(backup,output):
    backup,output=Path(backup),Path(output)
    ancestor=output.absolute().parent
    while not ancestor.exists() and not ancestor.is_symlink():ancestor=ancestor.parent
    with directory(ancestor):pass
    database,manifest,metadata=verified_snapshot(backup)
    # Verify everything before creating the new isolated destination.
    result=restore_isolated(backup,output)
    if metadata is not None:
        write_json(output/'HUB_INCOMPLETE.json',{'state':'incomplete','scope':'hub_database_notes_vault_and_library'})
        for relative,info in metadata['files'].items():copy_file(SnapshotFiles(backup),output,relative,info)
        if SnapshotFiles(output).inventory()!=metadata['files']:raise HubError('NOTE_SNAPSHOT_INVALID',409)
        check_references(output/DATABASE_NAME,output)
        (output/'db').mkdir(mode=0o700)
        (output/'config').mkdir(mode=0o700)
        with closing(open_readonly(output/DATABASE_NAME)) as con:
            con.execute('BEGIN');copy_database(con,output/'db'/'hub.db')
            matching_report(manifest['database']['audit'],audit_connection(con))
        matching_report(manifest['database']['audit'],audit_database(output/'db'/'hub.db'))
        write_json(output/'hub-restore-verification.json',{'format_version':1,'state':'verified',
            'scope':'hub_database_notes_vault_and_library','file_count':len(metadata['files']),'revision':metadata['revision']})
        sync_directory(output/'db');sync_directory(output)
        (output/'HUB_INCOMPLETE.json').unlink();sync_directory(output)
    return result
