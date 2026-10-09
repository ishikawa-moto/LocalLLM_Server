"""Bounded local-only model integration smoke tests; does not execute model-supplied code."""
import pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / 'src'))
from site_settings import LOOPBACK, site_port
import argparse, json, pathlib, time, urllib.request, urllib.error
ROOT=pathlib.Path(__file__).resolve().parents[1]
KEY=json.loads((ROOT/'config/api-keys.json').read_text(encoding='utf-8'))['llm']
BASE=('http://' + LOOPBACK + ':' + str(site_port('llm')))

def post(path,body,timeout=180):
    req=urllib.request.Request(BASE+path,data=json.dumps(body).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+KEY})
    start=time.perf_counter()
    with urllib.request.urlopen(req,timeout=timeout) as r: result=json.load(r)
    return result,time.perf_counter()-start

def text(response):
    return ''.join(c.get('text','') for item in response.get('output',[]) for c in item.get('content',[]) if c.get('type')=='output_text')

def run():
    report={'started_at':time.time(),'tests':[]}
    for _ in range(120):
        try:
            with urllib.request.urlopen(BASE+'/health',timeout=2) as r:
                if json.load(r).get('status')=='ok': break
        except OSError: time.sleep(1)
    else: raise RuntimeError('Server did not become healthy within 120 seconds')
    response,elapsed=post('/v1/responses',{'model':'local-qwen38','input':'Reply with exactly LOCAL_READY.','max_output_tokens':256})
    output=text(response)
    report['tests'].append({'name':'responses_text','seconds':elapsed,'text':output,'pass':'LOCAL_READY' in output})
    tools=[{'type':'function','name':'read_fixture','description':'Read a named fixture. Call with alpha first, then beta after seeing alpha, then gamma after seeing beta.','parameters':{'type':'object','properties':{'name':{'type':'string','enum':['alpha','beta','gamma']}},'required':['name'],'additionalProperties':False}}]
    history=[{'role':'user','content':'Read alpha using the tool, then follow the returned instructions. Do not invent fixture values.'}]
    calls=[]; final=''; started=time.perf_counter()
    values={'alpha':'alpha=7. Now call read_fixture for beta.','beta':'beta=11. Now call read_fixture for gamma.','gamma':'gamma=13. Reply with total 31 and make no more tool calls.'}
    for step in range(5):
        result,_=post('/v1/responses',{'model':'local-qwen38','input':history,'tools':tools,'max_output_tokens':512})
        history.extend(result.get('output',[]))
        tool_calls=[i for i in result.get('output',[]) if i.get('type')=='function_call']
        if not tool_calls: final=text(result); break
        for item in tool_calls:
            args=json.loads(item['arguments']); name=args.get('name')
            if item.get('name')!='read_fixture' or name not in values: raise RuntimeError('Unexpected tool call')
            calls.append(name)
            history.append({'type':'function_call_output','call_id':item['call_id'],'output':values[name]})
    report['tests'].append({'name':'three_tool_round_trips','seconds':time.perf_counter()-started,'calls':calls,'final':final,'pass':calls==['alpha','beta','gamma'] and '31' in final})
    req=urllib.request.Request(BASE+'/v1/responses',data=json.dumps({'model':'local-qwen38','input':'Count from 1 to 20.','stream':True,'max_output_tokens':256}).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+KEY})
    started=time.perf_counter(); first=None; events=[]
    with urllib.request.urlopen(req,timeout=180) as r:
        for line in r:
            if line.startswith(b'data: '):
                raw=line[6:].strip()
                if raw==b'[DONE]': break
                value=json.loads(raw); event=value.get('type',''); events.append(event)
                if event=='response.output_text.delta' and first is None: first=time.perf_counter()-started
    report['tests'].append({'name':'streaming','ttft_seconds':first,'event_types':sorted(set(events)),'pass':first is not None and 'response.completed' in events})
    report['all_pass']=all(t['pass'] for t in report['tests'])
    (ROOT/'logs/llm-smoke.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps(report,indent=2,ensure_ascii=False),flush=True)

if __name__=='__main__': run()
