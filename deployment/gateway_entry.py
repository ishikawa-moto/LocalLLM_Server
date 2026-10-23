"""Gateway release entry: verify frozen code, then use the stable local data root."""
import argparse,hashlib,json,os,runpy,sys
from pathlib import Path
def safe(path,must_exist=True):
    path=Path(path).absolute()
    for p in (path,*path.parents):
        if not p.exists() and not must_exist:continue
        st=p.lstat()
        if p.is_symlink() or getattr(st,'st_file_attributes',0)&0x400:raise ValueError('Reparse path refused')
    return path
def verify(pointer):
    pointer=safe(pointer)
    info=json.loads(pointer.read_bytes())
    if info.get('schema')!='localbrain.gateway-release-pointer.v1':raise ValueError('Unknown pointer')
    release=safe(info['release_root']);data=safe(info['data_root'])
    if release==data or release in data.parents or data in release.parents:raise ValueError('Code and data roots overlap')
    manifest_path=safe(release/'release-manifest.json')
    raw=manifest_path.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=info['manifest_sha256']:raise ValueError('Manifest mismatch')
    manifest=json.loads(raw)
    if manifest['source_commit']!=info['source_commit']:raise ValueError('Version mismatch')
    seen=set()
    for record in manifest['files']:
        name=record['path']
        if name in seen:raise ValueError('Duplicate release file')
        seen.add(name)
        path=safe(release/name)
        if release not in path.parents:raise ValueError('Release escape')
        if hashlib.sha256(path.read_bytes()).hexdigest()!=record['sha256']:raise ValueError('Release bytes changed')
    actual=set()
    for path in release.rglob('*'):
        safe(path)
        if path.is_file():actual.add(path.relative_to(release).as_posix())
    if actual!=seen|{'release-manifest.json'}:raise ValueError('Unexpected release file inventory')
    required={'src/control_gateway.py','src/slack_gateway.py','src/site_settings.py','Manage-LocalBrain.ps1','Site-Settings.ps1'}
    if not required<=seen:raise ValueError('Incomplete release')
    return info,release,data
def main():
    p=argparse.ArgumentParser();p.add_argument('--pointer',type=Path,required=True);p.add_argument('--verify-only',action='store_true')
    a=p.parse_args();info,release,data=verify(a.pointer)
    if a.verify_only:
        print(json.dumps(dict(status='VERIFIED',source_commit=info['source_commit'],manifest_sha256=info['manifest_sha256'],models_started=0)))
        return
    os.environ.pop('PYTHONPATH',None)
    os.environ.pop('PYTHONHOME',None)
    os.environ['LOCALBRAIN_DATA_ROOT']=str(data)
    os.environ['LOCALBRAIN_CODE_ROOT']=str(release)
    os.environ['LOCALBRAIN_RELEASE_COMMIT']=info['source_commit']
    os.environ['PYTHONDONTWRITEBYTECODE']='1'
    sys.path.insert(0,str(release/'src'))
    runpy.run_path(str(release/'src/control_gateway.py'),run_name='__main__')
if __name__=='__main__':main()
