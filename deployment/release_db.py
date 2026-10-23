"""Typed read-only DB snapshots and one additive restricted-store migration."""
import argparse,hashlib,json,sqlite3,os
from pathlib import Path
DATABASES={'restricted':'runtime/slack-restricted/state.sqlite3','gateway':'runtime/gateway-jobs.sqlite3',
           'chat':'runtime/chat.sqlite3','index':'runtime/index.sqlite3','semantic':'runtime/semantic.sqlite3'}
RETRY_DDL='CREATE TABLE retry_links(previous_id TEXT PRIMARY KEY, next_id TEXT UNIQUE NOT NULL, root_id TEXT NOT NULL, attempt_index INTEGER NOT NULL)'
def digest(v):
    return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()
def cell(v):
    if v is None:return ['null']
    if type(v)is int:return ['integer',str(v)]
    if type(v)is float:return ['real',v.hex()]
    if isinstance(v,str):return ['text',v]
    if isinstance(v,bytes):return ['blob',v.hex()]
    raise ValueError('Unknown SQLite cell')
def safe(path):
    path=Path(path).absolute()
    for p in (path,*path.parents):
        st=p.lstat()
        if p.is_symlink() or getattr(st,'st_file_attributes',0)&0x400:raise ValueError('Unsafe DB path')
    if not path.is_file():raise ValueError('Missing DB')
    return path
def snapshot_connection(db):
    integrity=db.execute('PRAGMA integrity_check').fetchall()
    if integrity!=[('ok',)]:raise ValueError('Integrity failed')
    schema=db.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name,tbl_name,sql').fetchall()
    tables={}
    for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall():
        quoted='"'+name.replace('"','""')+'"'
        rows=sorted(digest([cell(v) for v in row]) for row in db.execute('SELECT * FROM '+quoted))
        tables[name]=dict(rows=len(rows),typed_rows_sha256=digest(rows))
    record=dict(user_version=db.execute('PRAGMA user_version').fetchone()[0],
                schema_sha256=digest(schema),tables=tables,integrity='ok')
    record['logical_sha256']=digest(record)
    return record
def snapshot_file(path):
    path=safe(path)
    db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=3)
    try:
        db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
        return snapshot_connection(db)
    finally:db.close()
def snapshot_root(root):
    root=Path(root).absolute()
    result={label:snapshot_file(root/relative) for label,relative in DATABASES.items()}
    return dict(schema='localbrain.all-table-snapshot.v1',databases=result,
                snapshot_scope='Independent consistent read-only transaction per database; includes sqlite_sequence and shadow tables',
                aggregate_sha256=digest(result))
def verify_retry_table(db):
    objects=db.execute("SELECT type,name,sql FROM sqlite_master WHERE tbl_name='retry_links' ORDER BY type,name").fetchall()
    table=[row for row in objects if row[0]=='table']
    normalize=lambda sql:' '.join(sql.split()).casefold()
    if len(table)!=1 or normalize(table[0][2])!=normalize(RETRY_DDL):
        raise ValueError('Unknown retry DDL')
    if any(row[0] not in ('table','index') or (row[0]=='index' and row[2] is not None) for row in objects):
        raise ValueError('Unknown retry triggers or explicit indexes')
    if db.execute('PRAGMA foreign_key_list(retry_links)').fetchall():
        raise ValueError('Unknown retry foreign key')
    columns=[(v[1],v[2],v[3],v[5]) for v in db.execute('PRAGMA table_info(retry_links)')]
    if columns!=[('previous_id','TEXT',0,1),('next_id','TEXT',1,0),('root_id','TEXT',1,0),('attempt_index','INTEGER',1,0)]:
        raise ValueError('Unknown retry schema')
    unique=set()
    indexes=db.execute('PRAGMA index_list(retry_links)').fetchall()
    if len(indexes)!=2 or any(row[2]!=1 or row[3] not in ('pk','u') or row[4]!=0 for row in indexes):
        raise ValueError('Unknown retry indexes')
    for row in indexes:
        if row[2]:
            quoted='"'+row[1].replace('"','""')+'"'
            unique.add(tuple(v[2] for v in db.execute('PRAGMA index_info('+quoted+')')))
    if unique!={('previous_id',),('next_id',)}:raise ValueError('Retry index mismatch')
def migrate(root,expected):
    path=safe(Path(root)/DATABASES['restricted'])
    db=sqlite3.connect(path,timeout=3)
    try:
        db.execute('PRAGMA synchronous=FULL');db.execute('BEGIN IMMEDIATE')
        before=snapshot_connection(db)
        if before!=expected:raise ValueError('Restricted baseline drift')
        columns=[(v[1],v[2],v[3],v[5]) for v in db.execute('PRAGMA table_info(runs)')]
        if columns!=[('id','TEXT',0,1),('hash','TEXT',1,0),('state','TEXT',1,0),
                     ('result','TEXT',0,0),('session_id','TEXT',1,0),('operation','TEXT',1,0)]:
            raise ValueError('Unknown runs schema')
        created='retry_links' not in before['tables']
        if created:db.execute(RETRY_DDL)
        verify_retry_table(db)
        after=snapshot_connection(db)
        for name,rows in before['tables'].items():
            if after['tables'].get(name)!=rows:raise ValueError('Existing rows changed')
        if set(after['tables'])!=set(before['tables'])|{'retry_links'}:raise ValueError('Unexpected tables')
        if after['user_version']!=before['user_version']:raise ValueError('Version changed')
        if created and after['tables']['retry_links']['rows']!=0:raise ValueError('Unexpected retry rows')
        db.commit()
        return dict(status='ADDITIVE_MIGRATION_VERIFIED',created=created,before=before,after=after,
                    existing_rows_unchanged=True,user_version_unchanged=True,automatic_restore=False)
    except Exception:
        db.rollback() # Only uncommitted transaction rollback; never a saved DB restoration.
        raise
    finally:db.close()
def main():
    p=argparse.ArgumentParser()
    p.add_argument('--mode',choices=('Describe','Snapshot','Migrate'),default='Describe')
    p.add_argument('--root',type=Path);p.add_argument('--before',type=Path);p.add_argument('--before-sha256')
    a=p.parse_args()
    if a.mode=='Describe':print(json.dumps(dict(mode='Describe',database_writes=0)));return
    if a.mode=='Snapshot':result=snapshot_root(a.root)
    else:
        raw=a.before.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=a.before_sha256:raise ValueError('Baseline file drift')
        before=json.loads(raw)
        result=migrate(a.root,before['databases']['restricted'])
    print(json.dumps(result,sort_keys=True))
if __name__=='__main__':
    try:main()
    except Exception:
        print(json.dumps(dict(status='HOLD_DATABASE_CHECK_FAILED_NO_PRIVATE_ERROR')))
        raise SystemExit(2)
