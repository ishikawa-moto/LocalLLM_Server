"""Stage-local normal-test runner; does not load production configuration."""
import json, os, socket, sys, tempfile, unittest
from pathlib import Path
repo=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(repo/'src'))
from site_settings import LOOPBACK
stage=Path(tempfile.mkdtemp(prefix='localbrain-public-tests-'))
sys.dont_write_bytecode=True
scratch=stage/'test-temp'
scratch.mkdir(exist_ok=True)
tempfile.tempdir=str(scratch)
os.environ['TEMP']=os.environ['TMP']=str(scratch)
os.environ['PYTHONDONTWRITEBYTECODE']='1'
os.environ['PYTHONPATH']=str(repo/'src')
ports={}
for service in ('llm','proxy','chat','brain','embedding','gateway','bridge','smb'):
    with socket.socket() as sock:
        sock.bind((LOOPBACK,0));ports[service+'_port']=sock.getsockname()[1]
site=repo/'config/private-site.json'
if site.exists():
    raise SystemExit('Fixture config already exists: inspect and remove only the previous generated fixture first')
fixture_text=json.dumps(dict(ports,client_address='localbrain-client',server_address='localbrain-server'))
site.write_text(fixture_text)
original_connect,original_bind=socket.socket.connect,socket.socket.bind
allowed=set()
def bind(sock,address):
    if not isinstance(address,tuple) or address[0] not in (LOOPBACK,'localhost','::1'):
        raise AssertionError('Non-loopback test bind forbidden')
    result=original_bind(sock,address)
    allowed.add(sock.getsockname()[1])
    return result
def connect(sock,address):
    if not isinstance(address,tuple) or address[0] not in (LOOPBACK,'localhost','::1') or address[1] not in allowed:
        raise OSError('Only public test fixture listeners are allowed')
    return original_connect(sock,address)
socket.socket.connect=connect
socket.socket.bind=bind
sys.path[:0]=[str(repo/'src'),str(repo/'tests')]
try:
    pattern=sys.argv[1] if len(sys.argv)>1 else 'test_*.py'
    suite=unittest.defaultTestLoader.discover(str(repo/'tests'),pattern=pattern)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    print(json.dumps(dict(tests=result.testsRun,failures=len(result.failures),errors=len(result.errors),skipped=len(result.skipped),
                         network_scope='test-bound loopback listeners only; no production model',production_config_loaded=False)))
    status=0 if result.wasSuccessful() else 1
finally:
    # This exact file was generated here and was never copied from production.
    if site.read_text() != fixture_text: raise RuntimeError('Fixture changed; retained for inspection')
    site.unlink()
sys.exit(status)
