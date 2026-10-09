"""Create a transfer ZIP with an encrypted PFX and no API keys/plaintext private keys."""
from __future__ import annotations
from site_settings import site_port

import argparse
import getpass
import shutil
import tempfile
import zipfile
from pathlib import Path
import json
from site_settings import site_value

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import pkcs12

ROOT=Path(__file__).resolve().parents[1]


def client_network_settings(template, root=ROOT):
    settings = dict(template)
    settings['gatewayBaseUrl'] = f"https://{site_value('server_address', 'localbrain-server', root)}:{site_port('gateway', root)}"
    settings['bridgePort'] = site_port('bridge', root)
    return settings

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('output',type=Path); args=parser.parse_args()
    tls=ROOT/'config'/'tls'; cert_file=tls/'client.pem'; key_file=tls/'client-key.pem'
    if not cert_file.exists() or not key_file.exists():
        legacy=ROOT/'client-package'/'certs'
        cert_file=legacy/'client.pem'; key_file=legacy/'client-key.pem'
    password=getpass.getpass('New transfer PFX password: ').encode(); confirm=getpass.getpass('Confirm password: ').encode()
    if len(password)<12 or password!=confirm: raise ValueError('Password must match and contain at least 12 characters')
    certificate=x509.load_pem_x509_certificate(cert_file.read_bytes())
    private=serialization.load_pem_private_key(key_file.read_bytes(),password=None)
    pfx=pkcs12.serialize_key_and_certificates(b'LocalBrain Client',private,certificate,None,serialization.BestAvailableEncryption(password))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='localbrain-client-') as temp:
        target=Path(temp)/'LocalBrain-Client'; shutil.copytree(ROOT/'client-package',target,
            ignore=shutil.ignore_patterns('certs','bin','obj','client-config.json','localbrain_bridge.py','localbrain_mcp.py','private-*'))
        settings_path=target/'clientsettings.template.json'
        settings=client_network_settings(json.loads(settings_path.read_text(encoding='utf-8')))
        settings_path.write_text(json.dumps(settings,indent=2)+'\n',encoding='utf-8')
        certs=target/'certs'; certs.mkdir(); shutil.copy2(tls/'ca.pem',certs/'ca.pem'); (certs/'client.pfx').write_bytes(pfx)
        with zipfile.ZipFile(args.output,'w',zipfile.ZIP_DEFLATED) as archive:
            for path in target.rglob('*'):
                if path.is_file(): archive.write(path,path.relative_to(Path(temp)))
    print(f'Encrypted client package: {args.output}')


if __name__=='__main__': main()
