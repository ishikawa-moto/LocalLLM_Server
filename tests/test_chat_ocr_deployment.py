"""Public deployment contract fixtures; no SYSTEM tasks, private configuration or requests."""
import copy
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import subprocess
import sys

REPO=Path(__file__).resolve().parents[1]
def module(name,file):
    spec=importlib.util.spec_from_file_location(name,REPO/'deployment'/file)
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value
checks=module('ocr_checks_fixture','chat_ocr_checks.py')
entry=module('ocr_entry_fixture','chat_ocr_entry.py')
def sha(raw):return hashlib.sha256(raw).hexdigest()

class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.now=dt.datetime.now(dt.timezone.utc)
        self.intent=dict(source_commit='a'*40,manifest_sha256='b'*64,script_sha256='c'*64,
                         client_source='d'*40,client_manifest_sha256='e'*64)
        self.go=dict(phase='ClientMaintenanceConfirmedChatOcrApplyGO',intent_sha256='f'*64,
                     **self.intent,client_active_requests=0,client_queued_requests=0,all_chat_slack_continue_idle=True,
                     no_new_requests_until_finished=True,human_approval_reference='public-fixture-direct-human',created_utc=self.now.isoformat())
    def test_exact_fresh_go(self):checks.validate_go(self.go,self.intent,'f'*64,self.now)
    def test_bool_unknown_busy_or_missing_approval_not_zero(self):
        for key,value in [('client_active_requests',False),('client_queued_requests',None),('client_queued_requests',1),
                          ('no_new_requests_until_finished',1),('all_chat_slack_continue_idle',False),('human_approval_reference','')]:
            v=dict(self.go);v[key]=value
            with self.assertRaises(ValueError,msg=key):checks.validate_go(v,self.intent,'f'*64,self.now)
    def test_source_manifest_script_and_client_binding(self):
        for key in ('source_commit','manifest_sha256','script_sha256','client_source','client_manifest_sha256','intent_sha256'):
            v=dict(self.go);v[key]='wrong'
            with self.assertRaises(ValueError,msg=key):checks.validate_go(v,self.intent,'f'*64,self.now)
    def test_expired_future_and_timezone_missing_go(self):
        for when in ((self.now-dt.timedelta(seconds=901)).isoformat(),(self.now+dt.timedelta(seconds=1)).isoformat(),self.now.replace(tzinfo=None).isoformat()):
            v=dict(self.go,created_utc=when)
            with self.assertRaises(ValueError):checks.validate_go(v,self.intent,'f'*64,self.now)
    def test_unknown_restricted_or_resource_cannot_be_idle(self):
        good=dict(state='READY',active_jobs=0,queued_jobs=0,brain=True,proxy=True,resources={'accepting_jobs':True},
                  restricted_activity=dict(available=True,stable=True,quiescent=True,active_requests=0,active_applications=0,nonterminal_runs=0))
        checks.idle(good)
        for key,value in [('state','FAILED'),('active_jobs',False),('queued_jobs',1),('brain',False),('resources',{}),('restricted_activity',{})]:
            bad=copy.deepcopy(good);bad[key]=value
            with self.assertRaises(ValueError,msg=key):checks.idle(bad)
        for key,value in [('stable',False),('active_requests',None),('active_applications',1),('nonterminal_runs',False)]:
            bad=copy.deepcopy(good);bad['restricted_activity'][key]=value
            with self.assertRaises(ValueError,msg=key):checks.idle(bad)

class EntryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.release=self.root/'release';self.release.mkdir();self.data=self.root/'data';self.data.mkdir()
        self.files=[]
        for name in ('src/startup_gate.py','src/chat_service.py','src/control_gateway.py','chat-web/ocr.js'):
            path=self.release/name;path.parent.mkdir(exist_ok=True);path.write_bytes(b'PUBLIC ONLY')
            self.files.append(dict(path=name,sha256=sha(path.read_bytes())))
        self.manifest=dict(schema='localbrain.code-release-manifest.v1',source_commit='a'*40,files=self.files)
        self.spec=dict(schema='localbrain.chat-ocr-startup-spec.v1',source_commit='a'*40,release_root=str(self.release),
                       data_root=str(self.data),entry_path=str(Path(entry.__file__).absolute()),
                       entry_sha256=sha(Path(entry.__file__).read_bytes()),activation_go=str(self.root/'activation-go.json'))
        self.path=self.root/'startup-spec.json'
    def tearDown(self):self.tmp.cleanup()
    def seal(self):
        raw=json.dumps(self.manifest).encode();(self.release/'release-manifest.json').write_bytes(raw)
        self.spec['manifest_sha256']=sha(raw);self.path.write_text(json.dumps(self.spec));return sha(self.path.read_bytes())
    def test_artifact_verification_does_not_open_or_create_history(self):
        spec,release,data=entry.verify(self.path,self.seal())
        self.assertEqual(data,self.data);self.assertEqual(list(self.data.iterdir()),[])
    def test_entry_verify_only_rechecks_spec_and_manifest_in_fresh_interpreter(self):
        pin=self.seal()
        args=[sys.executable,'-I','-B',entry.__file__,'--role','chat','--spec',str(self.path),'--spec-sha256',pin,'--verify-only']
        result=subprocess.run(args,capture_output=True,text=True,check=True)
        self.assertEqual(json.loads(result.stdout)['status'],'ARTIFACTS_VERIFIED')
        self.assertEqual(list(self.data.iterdir()),[])
        (self.release/'src/chat_service.py').write_bytes(b'CHANGED')
        self.assertNotEqual(subprocess.run(args,capture_output=True,text=True).returncode,0)
    def test_changed_spec_release_bytes_and_extra_inventory_fail(self):
        pin=self.seal();self.path.write_text('{}')
        with self.assertRaisesRegex(ValueError,'spec_drift'):entry.verify(self.path,pin)
        pin=self.seal();(self.release/'src/chat_service.py').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'bytes_drift'):entry.verify(self.path,pin)
        (self.release/'src/chat_service.py').write_bytes(b'PUBLIC ONLY');(self.release/'unexpected').write_bytes(b'EXTRA')
        with self.assertRaisesRegex(ValueError,'inventory'):entry.verify(self.path,pin)
    def test_manifest_escape_case_collision_and_root_overlap_fail(self):
        for name in ('../escape','C:/escape','src\\escape','SRC/STARTUP_GATE.PY'):
            self.manifest['files']=self.files+[dict(path=name,sha256='0'*64)]
            with self.assertRaises((ValueError,OSError)):entry.verify(self.path,self.seal())
        self.manifest['files']=self.files;self.spec['data_root']=str(self.release)
        with self.assertRaisesRegex(ValueError,'overlap'):entry.verify(self.path,self.seal())
    def test_entry_source_and_go_directory_binding_fail(self):
        self.spec['entry_sha256']='0'*64
        with self.assertRaisesRegex(ValueError,'entry'):entry.verify(self.path,self.seal())
        self.spec['activation_go']=str(self.data/'go.json')
        with self.assertRaisesRegex(ValueError,'go_outside'):entry.verify(self.path,self.seal())

if __name__=='__main__':unittest.main()
