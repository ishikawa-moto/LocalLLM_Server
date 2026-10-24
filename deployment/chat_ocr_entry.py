"""Fixed SYSTEM task entry for the explicitly bound Chat and Gateway pair."""
import argparse
import hashlib
import json
import os
import re
import runpy
import sys
from pathlib import Path

def digest(raw): return hashlib.sha256(raw).hexdigest()

def safe(path):
    path=Path(path)
    if not path.is_absolute() or '..' in path.parts: raise ValueError('unsafe_path')
    for p in (path,*path.parents):
        st=p.lstat()
        if p.is_symlink() or getattr(st,'st_file_attributes',0)&0x400: raise ValueError('reparse_refused')
    return path

def verify(spec_path, spec_sha):
    if not re.fullmatch('[0-9a-f]{64}',spec_sha): raise ValueError('spec_hash_required')
    spec_path=safe(spec_path); raw=spec_path.read_bytes()
    if digest(raw)!=spec_sha: raise ValueError('spec_drift')
    spec=json.loads(raw)
    if spec['schema']!='localbrain.chat-ocr-startup-spec.v1': raise ValueError('unknown_spec')
    if not re.fullmatch('[0-9a-f]{40}',spec['source_commit']): raise ValueError('invalid_source')
    release=safe(spec['release_root']); data=safe(spec['data_root'])
    if release==data or release in data.parents or data in release.parents: raise ValueError('root_overlap')
    if Path(spec['activation_go']).parent!=spec_path.parent: raise ValueError('go_outside_spec_directory')
    entry=safe(spec['entry_path'])
    if entry!=Path(__file__).absolute() or digest(entry.read_bytes())!=spec['entry_sha256']:
        raise ValueError('entry_drift')
    manifest=safe(release/'release-manifest.json').read_bytes()
    if digest(manifest)!=spec['manifest_sha256']: raise ValueError('manifest_drift')
    manifest=json.loads(manifest)
    if manifest['schema']!='localbrain.code-release-manifest.v1' or manifest['source_commit']!=spec['source_commit']:
        raise ValueError('manifest_version_drift')
    names=set(); folded=set()
    for record in manifest['files']:
        name=record['path']; relative=Path(name)
        if relative.is_absolute() or '..' in relative.parts or '\\' in name or ':' in name or name.casefold() in folded:
            raise ValueError('manifest_path_invalid')
        names.add(name);folded.add(name.casefold())
        path=safe(release/relative)
        if release not in path.parents or digest(path.read_bytes())!=record['sha256']:
            raise ValueError('release_bytes_drift')
    actual=set()
    for p in release.rglob('*'):
        safe(p)
        if p.is_file():actual.add(p.relative_to(release).as_posix())
    if actual!=names|{'release-manifest.json'}:raise ValueError('release_inventory_drift')
    if not {'src/startup_gate.py','src/chat_service.py','src/control_gateway.py','chat-web/ocr.js'}<=names:
        raise ValueError('incomplete_release')
    return spec,release,data

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--role',choices=('chat','gateway'),required=True)
    p.add_argument('--spec',type=Path,required=True);p.add_argument('--spec-sha256',required=True)
    p.add_argument('--verify-only',action='store_true'); a=p.parse_args()
    spec,release,data=verify(a.spec,a.spec_sha256)
    if a.verify_only:
        print(json.dumps(dict(status='ARTIFACTS_VERIFIED',source=spec['source_commit'],production_database_opened=False)))
        return
    os.environ.pop('PYTHONPATH',None);os.environ.pop('PYTHONHOME',None)
    overrides=('LOCALBRAIN_GATEWAY_PORT','LOCALBRAIN_IDLE_SECONDS','LOCALBRAIN_START_TIMEOUT','LOCALBRAIN_MAX_QUEUE',
               'LOCALBRAIN_MAX_START_ATTEMPTS','LOCALBRAIN_MAX_RAM_USAGE_GB','LOCALBRAIN_MIN_GPU_FREE_GB_FOR_START','LOCALBRAIN_ALLOWED_IPS')
    if any(name in os.environ for name in overrides):raise ValueError('unapproved_configuration_environment_override')
    os.environ['LOCALBRAIN_DATA_ROOT']=str(data);os.environ['LOCALBRAIN_CODE_ROOT']=str(release)
    os.environ['LOCALBRAIN_RELEASE_COMMIT']=spec['source_commit'];os.environ['LOCALBRAIN_GATE_REQUIRED']='1'
    os.environ['PYTHONDONTWRITEBYTECODE']='1'
    sys.path.insert(0,str(release/'src'))
    import startup_gate
    startup_gate.configure(spec,a.spec_sha256)
    for name in startup_gate.SCHEMAS:startup_gate.validate_schema(data/name)
    target=release/'src'/('chat_service.py' if a.role=='chat' else 'control_gateway.py')
    sys.argv=[str(target)]+(['--root',str(data)] if a.role=='chat' else [])
    runpy.run_path(str(target),run_name='__main__')

if __name__=='__main__':main()
