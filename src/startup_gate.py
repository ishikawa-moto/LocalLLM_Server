"""Optional, deployment-owned admission gate. Never creates or migrates an existing DB.

The verified entry installs the binding before importing either service. An entry
which requires this gate cannot fall back to legacy startup if its spec is lost.
"""
import datetime as dt
import hashlib
import json
import os
import sqlite3
import time
from pathlib import Path

_binding = None
_spec_sha = None

SCHEMAS = {
    'runtime/chat.sqlite3': {
        'conversations': ('id','owner','title','created'),
        'messages': ('id','conversation','role','content','metadata')},
    'runtime/gateway-jobs.sqlite3': {'jobs': ('request_id','role','state','updated_at')},
    'runtime/slack-restricted/state.sqlite3': {
        'retired_ids': ('kind','id','session_id'), 'retired_sessions': ('session_id','retired_at'),
        'retirements': ('request_id','session_id','result'),
        'runs': ('id','hash','state','result','session_id','operation'),
        'proposals': ('id','hash','payload'),
        'approvals': ('id','nonce','request_id','hash','result'),
        'retry_links': ('previous_id','next_id','root_id','attempt_index'),
        'rule_state': ('id','hash','payload'), 'accepted': ('id','payload','approval')},
}
REFERENCE_DDL = {
 'runtime/chat.sqlite3': '''CREATE TABLE conversations(id TEXT PRIMARY KEY,owner TEXT NOT NULL,title TEXT NOT NULL,created REAL NOT NULL);
 CREATE TABLE messages(id INTEGER PRIMARY KEY,conversation TEXT NOT NULL,role TEXT NOT NULL,content TEXT NOT NULL,metadata TEXT NOT NULL);
 CREATE INDEX owner_index ON conversations(owner);''',
 'runtime/gateway-jobs.sqlite3': '''CREATE TABLE jobs(request_id TEXT PRIMARY KEY,role TEXT,state TEXT NOT NULL,updated_at TEXT NOT NULL);''',
 'runtime/slack-restricted/state.sqlite3': '''
 CREATE TABLE retired_ids(kind TEXT NOT NULL,id TEXT NOT NULL,session_id TEXT NOT NULL,PRIMARY KEY(kind,id));
 CREATE TABLE retired_sessions(session_id TEXT PRIMARY KEY,retired_at INTEGER NOT NULL);
 CREATE TABLE retirements(request_id TEXT PRIMARY KEY,session_id TEXT NOT NULL,result TEXT NOT NULL);
 CREATE TABLE runs(id TEXT PRIMARY KEY,hash TEXT NOT NULL,state TEXT NOT NULL,result TEXT,session_id TEXT NOT NULL,operation TEXT NOT NULL);
 CREATE TABLE proposals(id TEXT PRIMARY KEY,hash TEXT NOT NULL,payload TEXT NOT NULL);
 CREATE TABLE approvals(id TEXT PRIMARY KEY,nonce TEXT UNIQUE NOT NULL,request_id TEXT UNIQUE NOT NULL,hash TEXT NOT NULL,result TEXT NOT NULL);
 CREATE TABLE retry_links(previous_id TEXT PRIMARY KEY,next_id TEXT UNIQUE NOT NULL,root_id TEXT NOT NULL,attempt_index INTEGER NOT NULL);
 CREATE TABLE rule_state(id TEXT PRIMARY KEY,hash TEXT NOT NULL,payload TEXT NOT NULL);
 CREATE TABLE accepted(id TEXT PRIMARY KEY,payload TEXT NOT NULL,approval TEXT NOT NULL);''',
}

def safe(path):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts: raise ValueError('unsafe_path')
    for part in (path, *path.parents):
        st = part.lstat()
        if part.is_symlink() or getattr(st,'st_file_attributes',0) & 0x400:
            raise ValueError('reparse_refused')
    return path

def identity(path):
    st = safe(path).stat()
    if not Path(path).is_file() or st.st_ino == 0: raise ValueError('file_identity_unavailable')
    return format(st.st_ino, '032x')

def configure(spec, spec_sha):
    global _binding, _spec_sha
    if _binding is not None: raise ValueError('gate_already_bound')
    root = safe(spec['data_root'])
    if set(spec['databases']) != set(SCHEMAS): raise ValueError('database_binding_incomplete')
    for name, expected in spec['databases'].items():
        if identity(root/name) != expected: raise ValueError('database_identity_changed')
    _binding, _spec_sha = spec, spec_sha

def enabled():
    if _binding is None and os.environ.get('LOCALBRAIN_GATE_REQUIRED'):
        raise ValueError('required_gate_not_bound')
    return _binding is not None

def opened():
    if not enabled(): return True
    try:
        value = json.loads(safe(_binding['activation_go']).read_bytes())
        if type(value) is not dict: return False
        if any(identity(Path(_binding['data_root'])/name) != expected
               for name,expected in _binding['databases'].items()): return False
        return (value.get('schema') == 'localbrain.chat-ocr-activation.v1'
                and value.get('spec_sha256') == _spec_sha
                and value.get('manifest_sha256') == _binding['manifest_sha256']
                and value.get('source') == _binding['source_commit']
                and value.get('data_root') == _binding['data_root']
                and value.get('databases') == _binding['databases']
                and value.get('existing_history_preserved') is True
                and value.get('loopback_authenticated_endpoints_verified') is True
                and isinstance(value.get('verified_utc'),str)
                and dt.datetime.fromisoformat(value['verified_utc'].replace('Z','+00:00')).tzinfo is not None)
    except (OSError,ValueError,KeyError,TypeError): return False

def wait():
    while not opened(): time.sleep(0.2)

def run_after_open(function, *args):
    wait()
    return function(*args)

def connect(path, timeout=10, read_only=False):
    """Existing-only connections; identity is rechecked on every open.

    SQLite may use an existing WAL/SHM; no immutable=1 shortcut discards WAL.
    Schema/row writes and journal-mode changes are never performed here.
    """
    if not enabled():
        if read_only:return sqlite3.connect(Path(path).absolute().as_uri()+'?mode=ro',uri=True,timeout=timeout)
        return sqlite3.connect(path,timeout=timeout)
    root = Path(_binding['data_root']); path = Path(path).absolute()
    name = path.relative_to(root).as_posix()
    if name not in SCHEMAS or identity(path) != _binding['databases'][name]:
        raise ValueError('database_identity_changed')
    db = sqlite3.connect(path.as_uri() + ('?mode=rw' if opened() and not read_only else '?mode=ro'),uri=True,timeout=timeout)
    if identity(path) != _binding['databases'][name]:
        db.close(); raise ValueError('database_identity_changed')
    return db

def validate_schema(path):
    if not enabled(): return
    name = Path(path).absolute().relative_to(Path(_binding['data_root'])).as_posix()
    db = connect(path,read_only=True)
    reference=sqlite3.connect(':memory:')
    try:
        # The initializer executes only in memory to derive its required layout.
        reference.executescript(REFERENCE_DDL[name])
        def unique_indexes(connection,table):
            return sorted((index[3],index[4],tuple(tuple(r) for r in connection.execute('PRAGMA index_xinfo("'+index[1].replace('"','""')+'")')))
                          for index in connection.execute('PRAGMA index_list("'+table+'")') if index[2])
        for table, expected in SCHEMAS[name].items():
            actual = db.execute('PRAGMA table_info("'+table+'")').fetchall()
            wanted = reference.execute('PRAGMA table_info("'+table+'")').fetchall()
            if actual != wanted or unique_indexes(db,table)!=unique_indexes(reference,table):
                raise ValueError('existing_schema_mismatch')
            if db.execute("SELECT count(*) FROM sqlite_master WHERE type='trigger' AND tbl_name=?",(table,)).fetchone()[0]:
                raise ValueError('unexpected_existing_trigger')
        if name == 'runtime/chat.sqlite3':
            if db.execute('PRAGMA journal_mode').fetchone()[0].lower() != 'wal':
                raise ValueError('existing_journal_mode_mismatch')
            if tuple(r[2] for r in db.execute('PRAGMA index_info(owner_index)')) != ('owner',):
                raise ValueError('existing_index_missing')
    finally: reference.close();db.close()

def metadata(role):
    if not enabled(): return None
    return dict(status='ACTIVE' if opened() else 'ACTIVATION_HELD',role=role,
                source=_binding['source_commit'],manifest_sha256=_binding['manifest_sha256'],
                spec_sha256=_spec_sha,pid=os.getpid(),existing_history_bound=True)

def readiness():
    if not enabled() or not opened(): return dict(status='CHAT_OCR_SERVER_NOT_READY')
    value = json.loads(safe(_binding['activation_go']).read_bytes())
    # The caller authenticates this very TLS socket (including role pin/IP).
    return dict(status='CHAT_OCR_SERVER_READY',source=_binding['source_commit'],
                existing_history_preserved=True,real_authenticated_endpoints_verified=True,
                verified_utc=value['verified_utc'],verification_scope='current authenticated mTLS readiness request; no OCR/model invocation')
