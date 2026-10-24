"""Public split-code/data fixture. Never imports production configuration."""
import json,os,subprocess,sys,tempfile,unittest
from pathlib import Path

class SplitRootTests(unittest.TestCase):
    def test_private_site_defaults_use_explicit_data_and_assets_use_code(self):
        repo=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);(root/'config').mkdir()
            site=dict(chat_port=12345,chat_allowed_subnets=['PUBLIC_SUBNET'],
                      chat_allowed_hosts=['public-host'],server_address='public-tls-name')
            (root/'config/private-site.json').write_text(json.dumps(site))
            env=dict(os.environ,LOCALBRAIN_DATA_ROOT=str(root),PYTHONDONTWRITEBYTECODE='1')
            code="import sys,json;sys.path.insert(0,sys.argv[1]);import chat_service as c;print(json.dumps({'data':str(c.ROOT),'code':str(c.CODE_ROOT),'hosts':c.DEFAULTS['allowed_hosts'],'subnets':c.DEFAULTS['allowed_subnets'],'tls':c.DEFAULTS['tls_server_name']}))"
            result=subprocess.run([sys.executable,'-I','-B','-c',code,str(repo/'src')],env=env,
                                  capture_output=True,text=True,check=True)
            value=json.loads(result.stdout)
            self.assertEqual(Path(value['data']),root);self.assertEqual(Path(value['code']),repo)
            self.assertEqual(value['hosts'],site['chat_allowed_hosts']);self.assertEqual(value['subnets'],site['chat_allowed_subnets'])
            self.assertEqual(value['tls'],site['server_address']);self.assertFalse((root/'runtime').exists())
