"""Narrow future-Apply checks. Default CLI verifies artifacts only.

Storage/endpoint modes require the separately approved Apply orchestration.
Only schema metadata, hashes, counts and process/release labels are returned.
"""
import argparse
import datetime as dt
import hashlib
import http.client
import ipaddress
import json
import socket
import ssl
import sys
from pathlib import Path

def sha(raw):return hashlib.sha256(raw).hexdigest()
def require(value,code):
    if not value:raise ValueError(code)
def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))
def safe(path,missing=False):
    path=Path(path)
    require(path.is_absolute() and '..' not in path.parts,'unsafe_path')
    for p in (path,*path.parents):
        try:st=p.lstat()
        except FileNotFoundError:
            if missing:continue
            raise
        require(not p.is_symlink() and not getattr(st,'st_file_attributes',0)&0x400,'reparse_refused')
    return path
def load(packet,intent_sha):
    packet=safe(packet);raw=safe(packet/'intent.json').read_bytes()
    require(sha(raw)==intent_sha,'intent_drift');intent=json.loads(raw)
    require(intent['schema']=='localbrain.chat-ocr-apply-intent.v1' and intent['packet']==str(packet),'scope_drift')
    require(sha(safe(packet/'Apply-ChatOcr.ps1').read_bytes())==intent['script_sha256'],'script_drift')
    require(sha(safe(packet/'chat_ocr_checks.py').read_bytes())==intent['checks_sha256'],'checks_drift')
    return packet,intent
def assets(packet,intent):
    manifest_raw=safe(packet/'release-manifest.json').read_bytes()
    require(sha(manifest_raw)==intent['manifest_sha256'],'manifest_drift')
    manifest=json.loads(manifest_raw);require(manifest['schema']=='localbrain.code-release-manifest.v1' and manifest['source_commit']==intent['source_commit'],'source_drift')
    payload=safe(packet/'payload');names=set();folded=set()
    for record in manifest['files']:
        name=record['path'];p=Path(name)
        require(not p.is_absolute() and '..' not in p.parts and '\\' not in name and ':' not in name and
                name.casefold() not in folded,'manifest_path_invalid')
        names.add(name);folded.add(name.casefold());path=safe(payload/p)
        require(payload in path.parents and sha(path.read_bytes())==record['sha256'],'payload_drift')
    actual=set()
    for p in payload.rglob('*'):
        safe(p)
        if p.is_file():actual.add(p.relative_to(payload).as_posix())
    require(actual==names,'payload_inventory_drift')
    records={r['path']:r['sha256'] for r in manifest['files']}
    for name,key in [('deployment/chat_ocr_entry.py','entry_sha256'),('deployment/chat_ocr_checks.py','checks_sha256'),
                     ('deployment/Apply-ChatOcr.ps1','script_sha256'),('deployment/release_fence.ps1','fence_sha256')]:
        require(records.get(name)==intent[key],'helper_release_binding')
    require(sha(safe(packet/'startup-spec.json').read_bytes())==intent['spec_sha256'],'spec_drift')
    spec=read(packet/'startup-spec.json')
    for key in ('source_commit','manifest_sha256','data_root','release_root','entry_path','entry_sha256','activation_go','databases'):
        require(spec[key]==intent[key],'spec_binding_drift')
    require(sha(safe(packet/'chat_ocr_entry.py').read_bytes())==intent['entry_sha256'],'entry_drift')
    require(sha(safe(intent['python']).read_bytes())==intent['python_sha256'],'runtime_drift')
    require(sha(safe(intent['powershell']).read_bytes())==intent['powershell_sha256'],'powershell_drift')
    require(sha(safe(packet/'chat-ocr.json').read_bytes())==intent['feature_sha256'] and read(packet/'chat-ocr.json')=={'enabled':True},'feature_drift')
    return dict(status='ARTIFACTS_VERIFIED',source=intent['source_commit'],file_count=len(names))
def validate_go(go,intent,intent_sha,now=None):
    now=now or dt.datetime.now(dt.timezone.utc)
    require(go.get('phase')=='ClientMaintenanceConfirmedChatOcrApplyGO','maintenance_phase')
    for key,expected in [('intent_sha256',intent_sha),('source_commit',intent['source_commit']),
                         ('manifest_sha256',intent['manifest_sha256']),('script_sha256',intent['script_sha256']),
                         ('client_source',intent['client_source']),('client_manifest_sha256',intent['client_manifest_sha256'])]:
        require(go.get(key)==expected,'maintenance_binding')
    for key in ('client_active_requests','client_queued_requests'):
        require(type(go.get(key)) is int and go[key]==0,'maintenance_counts')
    require(go.get('all_chat_slack_continue_idle') is True and go.get('no_new_requests_until_finished') is True,
            'maintenance_human_idle_required')
    require(isinstance(go.get('human_approval_reference'),str) and go['human_approval_reference'].strip(),'human_approval_required')
    created=dt.datetime.fromisoformat(go['created_utc'].replace('Z','+00:00'))
    require(created.tzinfo is not None and 0<=(now-created).total_seconds()<=900,'maintenance_expired')
def idle(value):
    require(value.get('state') in ('READY','STOPPED'),'gateway_unknown_or_busy')
    for key in ('active_jobs','queued_jobs'):
        require(type(value.get(key)) is int and value[key]==0,'gateway_busy')
    activity=value.get('restricted_activity',{})
    require(activity.get('available') is True and activity.get('stable') is True and activity.get('quiescent') is True,'restricted_unknown_or_busy')
    for key in ('active_requests','active_applications','nonterminal_runs'):
        require(type(activity.get(key)) is int and activity[key]==0,'restricted_busy')
    require(value.get('brain') is True and value.get('proxy') is True,'base_unhealthy')
    require(value.get('resources',{}).get('accepting_jobs') is True,'resource_unknown_or_busy')
def settings(intent):
    root=safe(intent['data_root']);site=read(root/'config/private-site.json')
    chat=read(root/'config/chat.json');gateway=read(root/'config/gateway.json')
    ports=dict(chat=chat.get('port',site['chat_port']),gateway=gateway.get('port',site['gateway_port']))
    for port in ports.values():require(type(port) is int and 0<port<=65535,'invalid_port')
    hosts=chat.get('allowed_hosts',site.get('chat_allowed_hosts',[]))
    require(isinstance(hosts,list) and hosts and all(isinstance(h,str) and h for h in hosts),'chat_host_unavailable')
    return root,site,chat,ports,hosts[0]
def request(intent,role,path):
    root,site,chat,ports,host=settings(intent)
    loopback=str(ipaddress.IPv4Address(socket.INADDR_LOOPBACK))
    if role=='gateway':
        context=ssl.create_default_context(cafile=str(safe(root/'config/tls/ca.pem')))
        context.load_cert_chain(str(safe(root/'config/tls/client.pem')),str(safe(root/'config/tls/client-key.pem')))
        name=chat.get('tls_server_name',site.get('server_address','localbrain-server'))
        conn=http.client.HTTPSConnection(name,ports[role],timeout=5,context=context)
        conn.sock=context.wrap_socket(socket.create_connection((loopback,ports[role]),timeout=5),server_hostname=name)
        headers={}
    else:
        conn=http.client.HTTPConnection(loopback,ports[role],timeout=5);headers={'Host':host}
    try:
        conn.request('GET',path,headers=headers);response=conn.getresponse();raw=response.read(128*1024+1)
        require(response.status==200 and len(raw)<=128*1024,'endpoint_unavailable')
        return json.loads(raw)
    finally:conn.close()
def storage(packet,intent):
    import types
    manifest_raw=safe(packet/'release-manifest.json').read_bytes()
    require(sha(manifest_raw)==intent['manifest_sha256'],'manifest_drift')
    records={r['path']:r['sha256'] for r in json.loads(manifest_raw)['files']}
    code=safe(packet/'payload/src/startup_gate.py').read_bytes()
    require(sha(code)==records['src/startup_gate.py'],'guard_source_drift')
    gate=types.ModuleType('chat_ocr_guard_check')
    exec(compile(code,'<verified-startup-guard>','exec'),gate.__dict__)
    spec_raw=safe(packet/'startup-spec.json').read_bytes()
    require(sha(spec_raw)==intent['spec_sha256'],'spec_drift')
    gate.configure(json.loads(spec_raw),intent['spec_sha256'])
    require(not gate.opened(),'already_activated')
    root=Path(intent['data_root']);result={}
    identities={name:gate.identity(root/name) for name in intent['existing_db_identities']}
    require(identities==intent['existing_db_identities'],'existing_database_identity_changed')
    for name in gate.SCHEMAS:
        path=root/name;gate.validate_schema(path);db=gate.connect(path)
        try:
            catalog=[tuple(r) for r in db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name")]
            require(db.execute('PRAGMA quick_check').fetchall()==[('ok',)],'existing_database_integrity')
        finally:db.close()
        files={}
        for p in (path,Path(str(path)+'-wal')):
            if p.exists() and p.stat().st_size:files[p.name]=sha(safe(p).read_bytes())
        result[name]=dict(file_id=gate.identity(path),catalog_sha256=sha(json.dumps(catalog,separators=(',',':')).encode()),files=files)
    return dict(schema='localbrain.chat-ocr-existing-storage.v1',databases=result,existing_db_identities=identities,rows_exported=0,
                note='SQLite may allocate SHM or an empty WAL during read-only access; existing main/nonempty WAL and schema are compared')
def probe_old(intent):
    value=request(intent,'gateway','/health');idle(value)
    require(value.get('release_commit')==intent['old_gateway_source'],'old_loaded_release_unknown')
    return dict(status='FRESH_IDLE_SNAPSHOT',state=value['state'],active=0,queued=0,restricted_quiescent=True,
                snapshot_only=True,global_admission_fenced=False,verified_utc=dt.datetime.now(dt.timezone.utc).isoformat())
def probe_held(intent):
    result={}
    for role in ('gateway','chat'):
        value=request(intent,role,'/health')
        require(value.get('status')=='ACTIVATION_HELD' and value.get('role')==role and
                value.get('source')==intent['source_commit'] and value.get('manifest_sha256')==intent['manifest_sha256'] and
                value.get('spec_sha256')==intent['spec_sha256'] and type(value.get('pid')) is int and
                value.get('existing_history_bound') is True,'held_process_binding')
        result[role]=value
    value=request(intent,'gateway','/v1/chat-ocr/readiness')
    require(value=={'status':'CHAT_OCR_SERVER_NOT_READY'},'held_authenticated_endpoint')
    return dict(status='HELD_ENDPOINTS_VERIFIED',processes=result,loopback_authenticated_endpoints_verified=True,
                clientpc_cross_machine_verified=False,model_requests=0)
def main():
    p=argparse.ArgumentParser();p.add_argument('--mode',choices=('Assets','Maintenance','Storage','OldProbe','HeldProbe'),default='Assets')
    p.add_argument('--packet',type=Path,required=True);p.add_argument('--intent-sha256',required=True)
    p.add_argument('--go-sha256');a=p.parse_args();packet,intent=load(a.packet,a.intent_sha256)
    assets(packet,intent)
    if a.mode=='Assets':result=assets(packet,intent)
    elif a.mode=='Maintenance':
        raw=safe(packet/'apply-go.json').read_bytes();require(sha(raw)==a.go_sha256,'maintenance_hash')
        validate_go(json.loads(raw),intent,a.intent_sha256);result=dict(status='FRESH_HUMAN_MAINTENANCE_GO')
    elif a.mode=='Storage':result=storage(packet,intent)
    elif a.mode=='OldProbe':result=probe_old(intent)
    else:result=probe_held(intent)
    print(json.dumps(result,separators=(',',':')))
if __name__=='__main__':main()
