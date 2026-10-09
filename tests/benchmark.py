import pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'src'))
from site_settings import LOOPBACK, site_port
import argparse, json, pathlib, statistics, subprocess, time, urllib.request
ROOT=pathlib.Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser(); parser.add_argument('--count',type=int,default=20); parser.add_argument('--label',default='llm-only'); args=parser.parse_args()
key=json.loads((ROOT/'config/api-keys.json').read_text(encoding='utf-8'))['llm']
samples=[]
for i in range(args.count+1):
    request={'prompt':'Explain why database transactions are useful. Give a clear numbered list with short examples.','n_predict':128,'temperature':0,'seed':42,'cache_prompt':False}
    req=urllib.request.Request(('http://' + LOOPBACK + ':' + str(site_port('llm')) + '/completion'),data=json.dumps(request).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+key})
    start=time.perf_counter()
    with urllib.request.urlopen(req,timeout=180) as response: result=json.load(response)
    elapsed=time.perf_counter()-start
    gpu=subprocess.run(['nvidia-smi','--query-gpu=memory.used,memory.free,utilization.gpu','--format=csv,noheader,nounits'],capture_output=True,text=True).stdout.strip()
    sample={'iteration':i,'warmup':i==0,'wall_seconds':elapsed,'timings':result.get('timings',{}),'gpu':gpu}
    samples.append(sample)
    (ROOT/'logs'/f'benchmark-{args.label}.json').write_text(json.dumps(samples,indent=2),encoding='utf-8')
    print(json.dumps(sample),flush=True)
