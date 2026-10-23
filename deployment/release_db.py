"""Typed full-DB evidence and bounded classification of index search counters."""
import argparse,copy,hashlib,json,re,sqlite3,os
from pathlib import Path
DATABASES={'restricted':'runtime/slack-restricted/state.sqlite3','gateway':'runtime/gateway-jobs.sqlite3',
           'chat':'runtime/chat.sqlite3','index':'runtime/index.sqlite3','semantic':'runtime/semantic.sqlite3'}
RETRY_DDL='CREATE TABLE retry_links(previous_id TEXT PRIMARY KEY, next_id TEXT UNIQUE NOT NULL, root_id TEXT NOT NULL, attempt_index INTEGER NOT NULL)'
METRICS_DDL='CREATE TABLE metrics (key TEXT PRIMARY KEY, value INTEGER NOT NULL DEFAULT 0)'
METRICS_POLICY='index-search-counters-v1'
SEARCH_DELTA_LIMIT=32
SEARCH_KEYS=frozenset(('search_requests','search_hits'))
METRIC_KEYS=SEARCH_KEYS|frozenset(('duplicate_sources_avoided','fact_promotions_blocked',
    'contradictions_detected','fact_promotions','drafts_blocked_for_secret','synthesis_rejections',
    'decision_rejections','librarian_failures','fact_drafts','conflict_drafts','fact-review_drafts',
    'synthesis_drafts','decision_drafts'))
SNAPSHOT_SCHEMA='localbrain.all-table-snapshot.v2'

def digest(v):
    return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()

def require(ok,code):
    if not ok:raise ValueError(code)

def cell(v):
    if v is None:return ['null']
    if type(v)is int:return ['integer',str(v)]
    if type(v)is float:return ['real',v.hex()]
    if isinstance(v,str):return ['text',v]
    if isinstance(v,bytes):return ['blob',v.hex()]
    raise ValueError('UNKNOWN_SQLITE_CELL')

def row_digest(rows):
    return digest(sorted(digest([cell(v) for v in row]) for row in rows))

def safe(path):
    path=Path(path).absolute()
    for p in (path,*path.parents):
        st=p.lstat()
        if p.is_symlink() or getattr(st,'st_file_attributes',0)&0x400:raise ValueError('UNSAFE_DB_PATH')
    if not path.is_file():raise ValueError('MISSING_DB')
    return path

def metrics_profile(db,rows):
    """Called on the same transaction and row buffer used for the full typed digest."""
    result=dict(policy=METRICS_POLICY,valid=False)
    try:
        objects=db.execute("SELECT type,name,sql FROM sqlite_master WHERE tbl_name='metrics' ORDER BY type,name").fetchall()
        normalize=lambda sql:' '.join(sql.split()).casefold()
        require(len(objects)==2 and objects[0]==('index','sqlite_autoindex_metrics_1',None)
                and objects[1][0:2]==('table','metrics') and
                normalize(objects[1][2])==normalize(METRICS_DDL),'METRICS_SCHEMA')
        require(db.execute('PRAGMA table_xinfo(metrics)').fetchall()==[
            (0,'key','TEXT',0,None,1,0),(1,'value','INTEGER',1,'0',0,0)],'METRICS_COLUMNS')
        require(db.execute('PRAGMA foreign_key_list(metrics)').fetchall()==[],'METRICS_FOREIGN_KEY')
        require(db.execute('PRAGMA index_list(metrics)').fetchall()==[
            (0,'sqlite_autoindex_metrics_1',1,'pk',0)],'METRICS_INDEX')
        require(db.execute('PRAGMA index_xinfo(sqlite_autoindex_metrics_1)').fetchall()==[
            (0,0,'key',0,'BINARY',1),(1,-1,None,0,'BINARY',0)],'METRICS_INDEX_COLUMNS')
        counters={}
        for row in rows:
            require(len(row)==2,'METRICS_ROW_SHAPE')
            key,value=row
            require(type(key)is str and key in METRIC_KEYS and key not in counters,'METRICS_UNKNOWN_KEY')
            require(type(value)is int and 0<=value<=2**63-1,'METRICS_COUNTER_TYPE_OR_RANGE')
            counters[key]=value
        require(counters.get('search_hits',0)<=counters.get('search_requests',0),'METRICS_HITS_EXCEED_REQUESTS')
        result.update(valid=True,counters=counters)
    except ValueError as exc:
        # Unknown key/SQL/value is never disclosed; the full typed table digest is still recorded.
        result['error']=str(exc)
    return result

def snapshot_connection(db,include_metrics=False):
    integrity=db.execute('PRAGMA integrity_check').fetchall()
    require(integrity==[('ok',)],'INTEGRITY_FAILED')
    schema=db.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name,tbl_name,sql').fetchall()
    tables={};profile=None
    for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall():
        quoted='"'+name.replace('"','""')+'"'
        rows=db.execute('SELECT * FROM '+quoted).fetchall()
        tables[name]=dict(rows=len(rows),typed_rows_sha256=row_digest(rows))
        if include_metrics and name=='metrics':profile=metrics_profile(db,rows)
    record=dict(user_version=db.execute('PRAGMA user_version').fetchone()[0],
                schema_sha256=digest(schema),tables=tables,integrity='ok')
    if include_metrics:record['metrics_profile']=profile or dict(policy=METRICS_POLICY,valid=False,error='METRICS_MISSING')
    record['logical_sha256']=digest(record)
    return record

def snapshot_file(path,include_metrics=False):
    path=safe(path)
    db=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=3)
    try:
        db.execute('PRAGMA query_only=ON');db.execute('BEGIN')
        return snapshot_connection(db,include_metrics)
    finally:db.close()

def snapshot_root(root):
    root=Path(root).absolute()
    result={label:snapshot_file(root/relative,label=='index') for label,relative in DATABASES.items()}
    return dict(schema=SNAPSHOT_SCHEMA,databases=result,
                snapshot_scope='Independent consistent read-only transaction per database; includes sqlite_sequence and shadow tables',
                aggregate_sha256=digest(result))

def validate_record(record,index=False):
    fields={'user_version','schema_sha256','tables','integrity','logical_sha256'}|({'metrics_profile'} if index else set())
    require(set(record)==fields,'SNAPSHOT_RECORD_SHAPE')
    require(record['integrity']=='ok' and type(record['user_version'])is int and record['user_version']>=0,'SNAPSHOT_INTEGRITY_OR_VERSION')
    require(re.fullmatch('[a-f0-9]{64}',record['schema_sha256']) is not None,'SNAPSHOT_SCHEMA_HASH')
    for table in record['tables'].values():
        require(set(table)=={'rows','typed_rows_sha256'} and type(table['rows'])is int and table['rows']>=0
                and re.fullmatch('[a-f0-9]{64}',table['typed_rows_sha256']) is not None,'SNAPSHOT_TABLE_RECORD')
    require(digest({k:v for k,v in record.items() if k!='logical_sha256'})==record['logical_sha256'],'SNAPSHOT_LOGICAL_HASH')
    if not index:return
    profile=record['metrics_profile']
    require(profile.get('valid') is True,profile.get('error','METRICS_PROFILE_INVALID'))
    require(set(profile)=={'policy','valid','counters'} and profile['policy']==METRICS_POLICY,'METRICS_PROFILE_SHAPE')
    counters=profile['counters']
    require(type(counters)is dict and set(counters)<=METRIC_KEYS,'METRICS_UNKNOWN_KEY')
    require(all(type(v)is int and 0<=v<=2**63-1 for v in counters.values()),'METRICS_COUNTER_TYPE_OR_RANGE')
    require(counters.get('search_hits',0)<=counters.get('search_requests',0),'METRICS_HITS_EXCEED_REQUESTS')
    require(record['tables'].get('metrics')==dict(rows=len(counters),typed_rows_sha256=row_digest(counters.items())),'METRICS_PROFILE_TYPED_DIGEST')

def validate_snapshot(snapshot):
    require(set(snapshot)=={'schema','databases','snapshot_scope','aggregate_sha256'} and snapshot['schema']==SNAPSHOT_SCHEMA,'SNAPSHOT_FORMAT')
    require(set(snapshot['databases'])==set(DATABASES),'SNAPSHOT_DATABASE_SET')
    for label,record in snapshot['databases'].items():validate_record(record,label=='index')
    require(snapshot['aggregate_sha256']==digest(snapshot['databases']),'SNAPSHOT_AGGREGATE_HASH')

def migration_after(before,migration):
    require(set(migration)=={'status','created','before','after','existing_rows_unchanged','user_version_unchanged','automatic_restore'},'MIGRATION_RECORD_SHAPE')
    require(migration['status']=='ADDITIVE_MIGRATION_VERIFIED' and type(migration['created'])is bool and
            migration['existing_rows_unchanged'] is True and migration['user_version_unchanged'] is True
            and migration['automatic_restore'] is False and migration['before']==before,'MIGRATION_RECORD_BINDING')
    after=migration['after'];validate_record(after)
    require(after['user_version']==before['user_version'],'MIGRATION_VERSION')
    require(set(after['tables'])==set(before['tables'])|{'retry_links'},'MIGRATION_TABLE_SET')
    require(all(after['tables'][name]==table for name,table in before['tables'].items()),'MIGRATION_OLD_ROWS')
    if migration['created']:
        require('retry_links' not in before['tables'] and after['tables']['retry_links']==
                dict(rows=0,typed_rows_sha256=row_digest([])),'MIGRATION_NEW_ROWS')
    else:require(after==before,'MIGRATION_NOOP_DRIFT')
    return after

def classify(before,current,migration=None):
    """All content exact except one bounded, baseline-anchored pair of search counters."""
    result=dict(policy=METRICS_POLICY,compatible=False,before_aggregate_sha256=before.get('aggregate_sha256'),
                current_aggregate_sha256=current.get('aggregate_sha256'),search_delta_limit=SEARCH_DELTA_LIMIT)
    try:
        validate_snapshot(before);validate_snapshot(current)
        require(before['snapshot_scope']==current['snapshot_scope'],'SNAPSHOT_SCOPE_DRIFT')
        for label,old in before['databases'].items():
            new=current['databases'][label]
            expected=migration_after(old,migration) if label=='restricted' and migration is not None else old
            if label!='index':
                require(expected==new,'NONMETRICS_DATABASE_DRIFT');continue
            require(old['schema_sha256']==new['schema_sha256'] and old['user_version']==new['user_version'],'INDEX_SCHEMA_OR_VERSION_DRIFT')
            require(set(old['tables'])==set(new['tables']),'INDEX_TABLE_SET_DRIFT')
            for name,table in old['tables'].items():
                if name!='metrics':require(table==new['tables'][name],'NONMETRICS_TABLE_DRIFT')
            a=old['metrics_profile']['counters'];b=new['metrics_profile']['counters']
            require(set(a)==set(b),'METRICS_KEY_SET_DRIFT')
            require(all(a[k]==b[k] for k in a.keys()-SEARCH_KEYS),'NONSEARCH_COUNTER_DRIFT')
            dq=b.get('search_requests',0)-a.get('search_requests',0)
            dh=b.get('search_hits',0)-a.get('search_hits',0)
            require(0<=dh<=dq<=SEARCH_DELTA_LIMIT,'SEARCH_COUNTER_DELTA')
            result.update(search_requests_delta=dq,search_hits_delta=dh)
        result.update(compatible=True,classification='BOUNDED_SEARCH_COUNTER_INCREASE' if result.get('search_requests_delta') else 'EXACT_CONTENT')
    except (ValueError,KeyError,TypeError) as exc:
        code=str(exc)
        result['reason_code']=code if re.fullmatch('[A-Z_]+',code) else 'INVALID_COMPARISON_RECORD'
    return result

def validate_legacy_business(legacy,current):
    """Require exact old business records and a current strict known metrics profile."""
    validate_snapshot(current)
    require(legacy.get('schema')=='localbrain.all-table-snapshot.v1' and
            set(legacy.get('databases',{}))==set(DATABASES) and
            legacy.get('aggregate_sha256')==digest(legacy['databases']),'LEGACY_SNAPSHOT_FORMAT')
    require(legacy.get('snapshot_scope')==current['snapshot_scope'],'LEGACY_SCOPE')
    for label,old in legacy['databases'].items():
        validate_record(old)
        new=current['databases'][label]
        if label!='index':require(old==new,'LEGACY_NONMETRICS_DATABASE_DRIFT');continue
        require(old['schema_sha256']==new['schema_sha256'] and old['user_version']==new['user_version']
                and set(old['tables'])==set(new['tables']),'LEGACY_INDEX_SCHEMA')
        require(all(table==new['tables'][name] for name,table in old['tables'].items() if name!='metrics'),'LEGACY_NONMETRICS_TABLE_DRIFT')
        require(old['tables']['metrics']['rows']==len(new['metrics_profile']['counters']),'LEGACY_METRICS_ROW_COUNT')

def adopt_legacy_metrics(legacy,current,reference):
    """Explicit audited operational rebaseline; it does not prove historical counter contents."""
    require(type(reference)is str and 0<len(reference.strip())<=1024,'ADOPTION_REFERENCE_REQUIRED')
    validate_legacy_business(legacy,current)
    return dict(status='AUDITED_LEGACY_METRICS_BASELINE_ADOPTION',policy=METRICS_POLICY,
                adoption_reference=reference,legacy_snapshot=legacy,observed_snapshot=current,
                adopted_baseline=copy.deepcopy(current),business_records_exact=True,
                current_metrics_schema_keys_types_valid=True,metrics_history_proven=False,
                producer_identity_proven=False,original_snapshot_modified=False,
                automatic_baseline_refresh=False)

def upgrade_legacy(legacy,current):
    """Explicit offline proof: never refresh an opaque old baseline without a typed hash match."""
    validate_legacy_business(legacy,current)
    old=legacy['databases']['index'];new=current['databases']['index']
    counters=new['metrics_profile']['counters']
    matches=[]
    if SEARCH_KEYS<=counters.keys():
        for dq in range(SEARCH_DELTA_LIMIT+1):
            for dh in range(dq+1):
                prior=dict(counters,search_requests=counters['search_requests']-dq,search_hits=counters['search_hits']-dh)
                if 0<=prior['search_hits']<=prior['search_requests'] and row_digest(prior.items())==old['tables']['metrics']['typed_rows_sha256']:
                    matches.append(prior)
    elif row_digest(counters.items())==old['tables']['metrics']['typed_rows_sha256']:matches.append(dict(counters))
    require(len(matches)==1,'LEGACY_TYPED_DIGEST_NOT_UNIQUELY_PROVEN')
    derived=copy.deepcopy(legacy);derived['schema']=SNAPSHOT_SCHEMA
    index=derived['databases']['index']
    index['metrics_profile']=dict(policy=METRICS_POLICY,valid=True,counters=matches[0])
    index['logical_sha256']=digest({k:v for k,v in index.items() if k!='logical_sha256'})
    derived['aggregate_sha256']=digest(derived['databases'])
    comparison=classify(derived,current)
    require(comparison['compatible'],'LEGACY_CLASSIFICATION_FAILED')
    return dict(status='LEGACY_TYPED_DIGEST_PROVEN',algorithm='bounded-search-preimage-v1',
                legacy_aggregate_sha256=legacy['aggregate_sha256'],observed_snapshot=current,
                derived_baseline=derived,comparison=comparison,
                producer_identity_proven=False,original_snapshot_modified=False)


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
def load_verified(path,sha):
    raw=Path(path).read_bytes()
    require(re.fullmatch('[a-f0-9]{64}',sha or '') is not None and hashlib.sha256(raw).hexdigest()==sha,'BASELINE_FILE_HASH')
    return json.loads(raw)

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--mode',choices=('Describe','Snapshot','Check','UpgradeLegacy','AdoptLegacyMetrics','Migrate'),default='Describe')
    p.add_argument('--root',type=Path);p.add_argument('--before',type=Path);p.add_argument('--before-sha256')
    p.add_argument('--migration',type=Path);p.add_argument('--migration-sha256')
    p.add_argument('--adoption-reference')
    a=p.parse_args()
    if a.mode=='Describe':
        print(json.dumps(dict(mode='Describe',database_writes=0,policy=METRICS_POLICY,search_delta_limit=SEARCH_DELTA_LIMIT)));return
    if a.mode=='Snapshot':result=snapshot_root(a.root)
    else:
        before=load_verified(a.before,a.before_sha256)
        if a.mode=='Migrate':result=migrate(a.root,before['databases']['restricted'])
        else:
            current=snapshot_root(a.root)
            if a.mode=='UpgradeLegacy':result=upgrade_legacy(before,current)
            elif a.mode=='AdoptLegacyMetrics':result=adopt_legacy_metrics(before,current,a.adoption_reference)
            else:
                migration=load_verified(a.migration,a.migration_sha256) if a.migration else None
                result=dict(current_snapshot=current,comparison=classify(before,current,migration))
    print(json.dumps(result,sort_keys=True))
if __name__=='__main__':
    try:main()
    except Exception as exc:
        code=str(exc)
        print(json.dumps(dict(status='HOLD_DATABASE_CHECK_FAILED_NO_PRIVATE_ERROR',
                             reason_code=code if re.fullmatch('[A-Z_]+',code) else 'PRIVATE_ERROR_SUPPRESSED')))
        raise SystemExit(2)
