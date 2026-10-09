"""Launch a separate local-provider Codex instance, preserving the desktop settings."""
import json, os, pathlib, shutil, subprocess, sys
ROOT=pathlib.Path(__file__).resolve().parents[1]
keyfile=ROOT/'config'/'api-keys.json'
if not keyfile.exists():
    print('Initialize LocalBrain first.',file=sys.stderr); sys.exit(1)
codex=shutil.which('codex')
if not codex:
    choices=list(((pathlib.Path(os.environ.get('LOCALAPPDATA', pathlib.Path.home()/'AppData'/'Local'))/'OpenAI'/'Codex'/'bin')).glob('*/codex.exe'))
    codex=str(max(choices,key=lambda p:p.stat().st_mtime)) if choices else None
if not codex: raise SystemExit('Codex CLI executable not found')
env=os.environ.copy()
env['PYTHONUTF8']='1'
env.setdefault('HOME', env.get('USERPROFILE', str(pathlib.Path.home())))
# CODEX_HOME is used for its documented purpose: this dedicated local-provider instance.
env['CODEX_HOME']=str(ROOT/'codex-home')
env['LOCALBRAIN_LLM_KEY']=json.loads(keyfile.read_text(encoding='utf-8'))['llm']
env['PATH']=str(ROOT/'commands')+os.pathsep+env.get('PATH','')
sys.exit(subprocess.call([codex,*sys.argv[1:]],env=env))
