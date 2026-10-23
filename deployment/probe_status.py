"""GET-only Gateway status; no model route or private text output."""
import argparse,http.client,ipaddress,json,socket,ssl
from pathlib import Path
def base_status(root):
    root=Path(root);site=json.loads((root/'config/private-site.json').read_bytes())
    loopback=str(ipaddress.IPv4Address(socket.INADDR_LOOPBACK))
    connection=http.client.HTTPConnection(loopback,site['brain_port'],timeout=3)
    try:
        connection.request('GET','/health');response=connection.getresponse();brain=response.status==200
    finally:connection.close()
    try:
        with socket.create_connection((loopback,site['proxy_port']),timeout=3):proxy=True
    except OSError:proxy=False
    return dict(schema='localbrain.release-base.v1',brain_healthy=brain,proxy_listening=proxy,model_requests=0)
def status(root):
    root=Path(root)
    site=json.loads((root/'config/private-site.json').read_bytes())
    chat=json.loads((root/'config/chat.json').read_bytes())
    tls=ssl.create_default_context(cafile=str(root/'config/tls/ca.pem'))
    tls.load_cert_chain(str(root/'config/tls/client.pem'),str(root/'config/tls/client-key.pem'))
    name=chat['tls_server_name'];connection=http.client.HTTPSConnection(name,site['gateway_port'],timeout=10,context=tls)
    def connect():
        raw=socket.create_connection((str(ipaddress.IPv4Address(socket.INADDR_LOOPBACK)),site['gateway_port']),timeout=10)
        connection.sock=tls.wrap_socket(raw,server_hostname=name)
    connection.connect=connect
    try:
        connection.request('GET','/v1/control/status');response=connection.getresponse();raw=response.read(262145)
        if response.status!=200 or len(raw)>262144:raise ValueError()
        value=json.loads(raw)
    finally:connection.close()
    active,queued=value.get('active_jobs'),value.get('queued_jobs')
    return dict(schema='localbrain.release-status.v1',state=value.get('state'),
        active_jobs=active if type(active)is int else None,queued_jobs=queued if type(queued)is int else None,
        safe_idle=type(active)is int and type(queued)is int and active==queued==0 and not value.get('active_task') and value.get('state')in('STOPPED','READY'),
        brain_healthy=value.get('brain') is True,proxy_listening=value.get('proxy') is True,
        release_commit=value.get('release_commit'),tls_verified=True,model_requests=0)
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--base-only',action='store_true');a=p.parse_args()
    try:print(json.dumps(base_status(a.root) if a.base_only else status(a.root)))
    except Exception:
        print(json.dumps(dict(status='HOLD_STATUS_UNAVAILABLE_NO_PRIVATE_ERROR')));raise SystemExit(2)
