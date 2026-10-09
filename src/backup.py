"""Snapshot the reproducible app and knowledge sources, excluding credentials and models."""
import argparse, datetime, hashlib, json, pathlib, zipfile, subprocess, tempfile
ROOT=pathlib.Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser(); parser.add_argument('destination',type=pathlib.Path); args=parser.parse_args()
destination=args.destination.resolve(); destination.mkdir(parents=True,exist_ok=True)
stamp=datetime.datetime.now().strftime('%Y%m%d-%H%M%S')
archive=destination/f'LocalBrain-{stamp}.zip'
paths=[]
excluded_sources=set()
for side in (ROOT/'SecondBrain'/'raw').rglob('*.meta.json'):
    try:
        metadata=json.loads(side.read_text(encoding='utf-8'))
        if metadata.get('quarantine_ref'):
            source=pathlib.Path(str(side)[:-len('.meta.json')]); excluded_sources.add(source.resolve()); excluded_sources.add(side.resolve())
    except (OSError,ValueError): pass
generated_parts={'__pycache__','.git','bin','obj'}
for folder in ('SecondBrain','src','commands','client-package','tests'):
    for path in (ROOT/folder).rglob('*'):
        in_quarantine=path.resolve().is_relative_to((ROOT/'SecondBrain'/'quarantine').resolve())
        if path.is_file() and not path.is_symlink() and not in_quarantine and path.resolve() not in excluded_sources and not generated_parts.intersection(path.parts) and 'certs' not in path.parts and path.name != 'client-config.json':
            paths.append(path)
for relative in ('README.md','OPERATIONS.md','IMPLEMENTATION-NOTES.md','Manage-LocalBrain.ps1','Install-Server.ps1','Install-Server-Admin.cmd',
                 'Prepare-Client-Package.ps1','Prepare-Client-Package.cmd',
                 'Create-Temporary-Client-Share.ps1','Remove-Temporary-Client-Share.ps1',
                 'Install-LocalBrain-On-ClientPC.cmd','Install-LocalBrain-On-ClientPC.ps1',
                 'REMOTE-SETUP.md','ClientPC-CONTINUE-SETUP.md','SECOND-BRAIN-SPEC.md','requirements-gateway.txt','requirements-secondbrain.txt','Local-Codex.cmd','config/download-manifest.json',
                 'config/llama-settings.json','config/gateway.json','config/secondbrain.json','config/python-runtime.json','config/private-site.json','codex-home/config.toml'):
    path=ROOT/relative
    if path.exists(): paths.append(path)
manifest={}; payloads={}
for path in sorted(paths):
    if not path.resolve().is_relative_to(ROOT): raise RuntimeError('Linked path outside source root')
    relative=path.relative_to(ROOT).as_posix(); data=path.read_bytes()
    manifest[relative]=hashlib.sha256(data).hexdigest(); payloads[relative]=data
if (ROOT/'SecondBrain/.git').exists():
    with tempfile.TemporaryDirectory(prefix='localbrain-bundle-',dir=destination) as tmp:
        bundle=pathlib.Path(tmp)/'SecondBrain.bundle'
        subprocess.run(['git','-c',f'safe.directory={ROOT / "SecondBrain"}','-C',str(ROOT/'SecondBrain'),'bundle','create',str(bundle),'--all'],check=True,capture_output=True)
        data=bundle.read_bytes(); payloads['history/SecondBrain.bundle']=data
        manifest['history/SecondBrain.bundle']=hashlib.sha256(data).hexdigest()
with zipfile.ZipFile(archive,'x',compression=zipfile.ZIP_DEFLATED) as z:
    for relative,data in payloads.items(): z.writestr(relative,data)
    z.writestr('BACKUP-MANIFEST.json',json.dumps(manifest,indent=2))
with zipfile.ZipFile(archive) as z:
    if z.testzip() is not None: raise RuntimeError('Archive integrity error')
    for relative,expected in manifest.items():
        if hashlib.sha256(z.read(relative)).hexdigest()!=expected: raise RuntimeError('Content verification error')
print(json.dumps({'archive':str(archive),'files':len(manifest),'verified':True,'excludes':['credentials','quarantine','models','derived index','cache','service logs']},indent=2))
