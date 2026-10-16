"""Bound decision reviews; no adoption or invented human approval here."""
import json
import pathlib
import re
import time
from approved_apply import ProposalStore, ApplyEngine, Rejected, require, safe_id, sha, encoded, atomic, HOST_OWNER
from draft_review import DraftReviewer, GatewayJudge, statements, MODEL, MAX_DRAFT_BYTES, MAX_SOURCE_BYTES, MAX_TOTAL_SOURCE_BYTES, MAX_SEGMENTS
from ingest import SECRET_PATTERNS
from knowledge_guard import guard
from site_settings import site_value

POLICY = 'decision-review-and-reaffirm-v1'
SCHEMA = 'localbrain.decision-review.v1'
MODES = ('proposal', 'user_request')


class DecisionReviewer:
    def __init__(self, brain, judge=None):
        self.brain=brain; self.judge=judge or GatewayJudge(brain)

    def policy(self):
        p=self.brain.root/'config'/'decision-review-policy.json'
        require(p.is_file() and not p.is_symlink() and not p.parent.is_symlink()
                and not getattr(p.parent,'is_junction',lambda:False)(), 'review_policy_disabled','Decision review policy is not installed.',409)
        v=json.loads(p.read_bytes())
        require(isinstance(v,dict) and set(v)=={'version','enabled','policy','host_policy_hash','projects'} and type(v['version']) is int and v['version']==1 and v['enabled'] is True and v['policy']==POLICY)
        require(isinstance(v['host_policy_hash'],str) and re.fullmatch('[a-f0-9]{64}',v['host_policy_hash']))
        require(isinstance(v['projects'],list) and v['projects'] and all(isinstance(project,str) and re.fullmatch(r'[\w.-]{1,80}',project) for project in v['projects']))
        return v

    def record_path(self, proposal_id):
        require(safe_id(proposal_id))
        return ApplyEngine(self.brain).runtime_path('decision-reviews/'+proposal_id+'.json')

    def read_record(self, proposal_id):
        p=self.record_path(proposal_id)
        if not p.exists():return None
        require(not p.is_symlink() and p.stat().st_size<=250000,'review_integrity','Invalid review record.',409)
        value=json.loads(p.read_bytes())
        require(isinstance(value,dict) and value.get('schema')==SCHEMA and value.get('proposal_id')==proposal_id,'review_integrity','Review identity mismatch.',409)
        return value

    def register(self, proposal_id, request_mode, host_policy_hash, task_id, workspace_id, client):
        with guard(self.brain):
            policy=self.policy(); proposal=ProposalStore(self.brain).read(proposal_id)
            require(client.get('approval_owner')==HOST_OWNER
                    and client.get('address')==site_value('client_address','localbrain-client',self.brain.root)
                    and isinstance(client.get('certificate_sha256'),str)
                    and re.fullmatch('[a-f0-9]{64}',client['certificate_sha256'])
                    and client.get('source')=='mTLS Gateway / trusted Continue approval','host_attestation_required','Owned Host registration required.',403)
            require(request_mode in MODES and safe_id(task_id) and safe_id(workspace_id))
            require(host_policy_hash==policy['host_policy_hash'] and proposal['payload']['project'] in policy['projects'],'review_policy_mismatch','Review scope differs from installed policy.',403)
            initial={'schema':SCHEMA,'policy':POLICY,'model':MODEL,'proposal_id':proposal_id,'proposal_hash':proposal['proposal_hash'],
                     'request_mode':request_mode,'host_policy_hash':host_policy_hash,'task_id':task_id,'workspace_id':workspace_id,
                     'client_certificate_sha256':client['certificate_sha256'],'state':'queued','issues':[],'review_passes':0}
            previous=self.read_record(proposal_id)
            if previous:
                keys=('proposal_hash','request_mode','host_policy_hash','task_id','workspace_id','client_certificate_sha256')
                require(all(previous[k]==initial[k] for k in keys),'review_id_conflict','Existing review belongs to another exact request.',409)
            else:atomic(self.record_path(proposal_id),encoded(initial))
        # Signal only the already committed proposal, using existing hidden/coalesced dispatch.
        from draft_review_queue import signal
        signal(self.brain)
        return self.status(proposal_id)

    def status(self, proposal_id):
        # Atomic JSON observations only: do not acquire a writer guard or trigger recovery.
        record=self.read_record(proposal_id)
        require(record is not None,'review_not_registered','No owned decision review.',409)
        engine=ApplyEngine(self.brain);result_path=engine.receipt(proposal_id)
        journal_path=engine.runtime_path('apply-transactions/'+proposal_id+'/journal.json')
        claim=engine.runtime_path('apply-claims/'+proposal_id+'.json')
        outcome=json.loads(result_path.read_bytes()) if result_path.is_file() else None
        journal=json.loads(journal_path.read_bytes()) if journal_path.is_file() else None
        if journal is not None:
            require(isinstance(journal,dict) and journal.get('request_id')==proposal_id and type(journal.get('finished')) is bool,
                    'review_integrity','Invalid apply journal observation.',409)
        apply_state=('exact_recovery_required' if journal is not None and (not journal['finished'] or not outcome)
                     else 'terminal' if outcome else 'in_progress' if claim.is_file() else 'not_started')
        return {'schema':SCHEMA,'proposal_id':proposal_id,'proposal_hash':record['proposal_hash'],
                'state':record['state'],'review_receipt':record,'review_receipt_hash':sha(encoded(record)),
                'apply_state':apply_state,'apply_result':outcome}

    def _input(self, record):
        policy=self.policy(); proposal=ProposalStore(self.brain).read(record['proposal_id']); payload=proposal['payload']
        require(record['policy']==POLICY and record['host_policy_hash']==policy['host_policy_hash'] and payload['project'] in policy['projects'])
        require(proposal['proposal_hash']==record['proposal_hash'],'proposal_hash_mismatch','Proposal changed.',409)
        raw=('# '+payload['title']+'\n\n'+payload['body']+'\n').encode('utf-8')
        require(len(raw)<=MAX_DRAFT_BYTES,'review_budget','Draft exceeds review budget.')
        parts=statements(raw.decode('utf-8'));require(1<=len(parts)<=MAX_SEGMENTS,'review_budget','Draft has too many statements.')
        refs=payload['references']; sources=[]
        # Automatic model proposals preserve two distinct immutable raw-source requirements.
        if record['request_mode']=='proposal':
            require(2<=len(refs)<=4,'needs_evidence','Automatic proposals require 2-4 distinct immutable raw sources.')
        else:require(1<=len(refs)<=4,'needs_evidence','Explicit save requests require current reference snapshots.')
        for ref in refs:
            snapshot=ApplyEngine(self.brain).target(ref,payload['project'],reference=True)
            if record['request_mode']=='proposal':DraftReviewer(self.brain,self.judge)._source(ref['path'])
            text=snapshot['content'];require(len(text.encode('utf-8'))<=MAX_SOURCE_BYTES,'review_budget','Source exceeds review budget.')
            require(not any(p.search(text) for p in SECRET_PATTERNS),'secret_detected','Source contains protected material.')
            match=re.fullmatch(r'L([1-9][0-9]*)(?:-L?([1-9][0-9]*))?',ref['relevant_location'])
            first,last=int(match[1]),int(match[2] or match[1]);excerpt='\n'.join(text.splitlines()[first-1:last])
            sources.append({'path':ref['path'],'text':excerpt,'sha256':snapshot['expected_hash'],'metadata_sha256':snapshot['expected_metadata_hash']})
        require(len({s['path'] for s in sources})==len(sources),'needs_evidence','References must be distinct.')
        if record['request_mode']=='proposal':require(len({s['sha256'] for s in sources})>=2,'needs_evidence','Raw source contents must differ.')
        require(sum(len(s['text'].encode('utf-8')) for s in sources)<=MAX_TOTAL_SOURCE_BYTES,'review_budget','Combined review sources exceed budget.')
        digest=sha(encoded({'proposal_hash':proposal['proposal_hash'],'sources':sources,'policy':POLICY,'mode':record['request_mode'],'model':MODEL}))
        return proposal,parts,sources,digest

    @staticmethod
    def vote(value,parts,sources,mode):
        require(isinstance(value,dict) and value.get('verdict')=='approve','reviewer_issues',str(value.get('reason','Reviewer did not approve.'))[:1000] if isinstance(value,dict) else 'Invalid reviewer response.')
        rows=value.get('items');require(isinstance(rows,list) and len(rows)==len(parts),'reviewer_issues','Reviewer omitted statements.')
        seen=set();result=[];by_path={s['path']:s for s in sources}
        for row in rows:
            require(isinstance(row,dict) and type(row.get('id')) is int and row['id'] in {p['id'] for p in parts} and row['id'] not in seen,'reviewer_issues','Invalid statement coverage.')
            quote=row.get('source_quote');source=by_path.get(row.get('source_path'))
            require(source and isinstance(quote,str) and len(quote.strip())>=20 and quote in source['text'],'reviewer_issues','Review citation is not an exact selected-source quote.')
            seen.add(row['id']);result.append({'id':row['id'],'source_path':source['path'],'source_quote':quote})
        if mode=='proposal':require(len({r['source_path'] for r in result})>=2,'reviewer_issues','Automatic approval needs coverage from two sources.')
        return result

    def candidates(self):
        folder=self.brain.runtime/'decision-reviews'
        if folder.exists():
            for p in sorted(folder.glob('*.json')):
                if safe_id(p.stem):yield self.brain.docs/'drafts/pending-review'/(p.stem+'.md')

    def is_unfinished(self,path):
        record=self.read_record(path.stem)
        return bool(record and record['state'] in ('queued','deferred'))

    def review(self,path):
        with guard(self.brain):
            record=self.read_record(path.stem); require(record is not None)
            if record['state'] not in ('queued','deferred'):return {'path':str(path),'state':'unchanged'}
            # Returned requests are terminal for these exact bytes: one return, never a review loop.
            try:proposal,parts,sources,input_hash=self._input(record)
            except Rejected as error:
                state='returned' if record['request_mode']=='user_request' and error.code in ('needs_evidence','review_budget') else 'needs_evidence'
                record.update(state=state,issues=[{'code':error.code,'message':error.reason}],return_count=1 if state=='returned' else 0,reviewed_at=int(time.time()))
                atomic(self.record_path(path.stem),encoded(record));return {'path':str(path),'state':state,'issues':record['issues']}
            # Resume only known unattempted work after a read-only preflight deferral.
            passes=record.get('passes',[])
            require(isinstance(passes,list) and len(passes)<=2 and record['review_passes']==len(passes),
                    'review_integrity','Completed review passes differ from their receipt.',409)
            if passes:
                require(record.get('input_hash')==input_hash,'stale_review','Inputs changed before deferred review resumed.',409)
                for vote in passes:self.vote({'verdict':'approve','items':vote},parts,sources,record['request_mode'])
            record.update(state='reviewing',input_hash=input_hash,started_at=int(time.time()))
            atomic(self.record_path(path.stem),encoded(record))
        packet={'draft_path':path.relative_to(self.brain.docs).as_posix(),'statements':parts,'sources':[{'path':s['path'],'text':s['text']} for s in sources]}
        try:
            for number in range(len(passes)+1,3):
                vote=self.vote(self.judge(packet,number),parts,sources,record['request_mode'])
                with guard(self.brain):
                    _,_,_,fresh=self._input(record);require(fresh==input_hash,'stale_review','Inputs changed during review.',409)
                    passes.append(vote)
                    record.update(review_passes=len(passes),passes=passes)
                    atomic(self.record_path(path.stem),encoded(record))
        except Rejected as error:
            record.update(state='returned' if record['request_mode']=='user_request' else 'needs_evidence',issues=[{'code':error.code,'message':error.reason}],
                          return_count=1 if record['request_mode']=='user_request' else 0,review_passes=len(passes),passes=passes,input_hash=input_hash,reviewed_at=int(time.time()))
            with guard(self.brain):
                _,_,_,fresh=self._input(record);require(fresh==input_hash,'stale_review','Inputs changed during review.',409)
                atomic(self.record_path(path.stem),encoded(record))
            return {'path':str(path),'state':record['state'],'issues':record['issues']}
        except Exception as error:
            # No blind repeat after an accepted/unknown model request or interrupted process.
            # Known read-only preflight busy/not-ready failures made no model request.
            deferred=isinstance(error,RuntimeError) and str(error) in ('Gateway busy or unavailable for background review','Background model is not ready')
            record.update(state='deferred' if deferred else 'review_unresolved',error_type=type(error).__name__)
            with guard(self.brain):
                if deferred:
                    _,_,_,fresh=self._input(record);require(fresh==input_hash,'stale_review','Inputs changed before preflight deferral.',409)
                atomic(self.record_path(path.stem),encoded(record))
            return {'path':str(path),'state':'deferred' if deferred else 'review_unresolved'}
        with guard(self.brain):
            _,_,_,fresh=self._input(record);require(fresh==input_hash,'stale_review','Inputs changed during review.',409)
            record.update(state='approved',review_passes=2,passes=passes,input_hash=input_hash,issues=[],return_count=0,reviewed_at=int(time.time()))
            atomic(self.record_path(path.stem),encoded(record))
        return {'path':str(path),'state':'approved'}


def validate_automatic_approval(brain,operation,payload,client):
    approval=payload['approval'];reviewer=DecisionReviewer(brain);policy=reviewer.policy()
    require(operation=='decision','invalid_automation_scope','Automatic policy applies to append-only bound decisions.')
    keys={'version','owner','approval_id','task_id','request_id','operation','proposal_id','proposal_hash','decision_hash','issued_at','expires_at','provenance','host_policy_hash','review_receipt_hash','reaffirmation_receipt_hash'}
    require(set(approval)==keys and type(approval['version']) is int and approval['version']==3 and approval['owner']==HOST_OWNER)
    from approved_apply import review_hash
    require(all(safe_id(approval[k]) for k in ('approval_id','task_id','request_id')) and approval['request_id']==payload['request_id'] and approval['operation']==operation)
    require(approval['decision_hash']==review_hash(operation,payload),'approval_mismatch','Content differs from Host intent.',409)
    require(type(approval['issued_at']) is int and type(approval['expires_at']) is int and 0<=approval['issued_at']<=int(time.time())<approval['expires_at'] and 0<approval['expires_at']-approval['issued_at']<=3600,'approval_expired','Expired automatic reservation.',409)
    require(approval['proposal_id']==payload['origin_proposal_id'] and approval['proposal_hash']==payload['expected_proposal_hash'])
    require(payload['request_id']==payload['origin_proposal_id'],'review_identity_mismatch','Automatic adoption must keep its registered proposal ID.',409)
    require(approval['host_policy_hash']==policy['host_policy_hash'],'review_policy_mismatch','Host policy differs.',409)
    record=reviewer.read_record(approval['proposal_id']);require(record is not None)
    require(record['task_id']==approval['task_id'] and record['proposal_hash']==approval['proposal_hash'] and record['client_certificate_sha256']==client['certificate_sha256'],'review_identity_mismatch','Review owner/content differs.',409)
    require(approval['review_receipt_hash']==sha(encoded(record)),'stale_review','Review receipt differs.',409)
    if approval['provenance']=='human-reaffirmed-returned-decision':
        require(record['request_mode']=='user_request' and record['state']=='returned' and record['return_count']==1 and isinstance(approval['reaffirmation_receipt_hash'],str) and re.fullmatch('[a-f0-9]{64}',approval['reaffirmation_receipt_hash']),'reaffirmation_required','Only a once-returned explicit request can be reaffirmed.',409)
        # The authenticated Windows Host owns the native-dialog human receipt. Never label this Reviewer PASS or FACT.
    else:
        expected='reviewer-approved-requested-decision' if record['request_mode']=='user_request' else 'reviewer-approved-model-proposal'
        require(approval['provenance']==expected and approval['reaffirmation_receipt_hash'] is None and record['state']=='approved' and record['review_passes']==2 and len(record.get('passes',[]))==2,'review_not_approved','Two validated Reviewer passes are required.',409)
        _,parts,sources,fresh=reviewer._input(record);require(fresh==record['input_hash'],'stale_review','Sources changed after review.',409)
        for vote in record['passes']:reviewer.vote({'verdict':'approve','items':vote},parts,sources,record['request_mode'])
    p=ApplyEngine(brain).path('log/approvals/'+approval['approval_id']+'.json',internal=True)
    require(not p.exists() and not ApplyEngine(brain).runtime_path('approval-consumptions/'+approval['approval_id']+'.json').exists(),'approval_used','Approval already consumed.',409)
    return approval,p


class CombinedReviewer:
    def __init__(self,brain,judge=None,require_committed=True):
        self.existing=DraftReviewer(brain,judge,require_committed=require_committed)
        decision_judge=GatewayJudge(brain,require_ready=False,deadline=judge.deadline) if isinstance(judge,GatewayJudge) else judge
        self.decisions=DecisionReviewer(brain,decision_judge)
    def candidates(self):yield from self.existing.candidates();yield from self.decisions.candidates()
    def is_unfinished(self,path):return self.decisions.is_unfinished(path) if path.parent.name=='pending-review' else self.existing.is_unfinished(path)
    def review(self,path):return self.decisions.review(path) if path.parent.name=='pending-review' else self.existing.review(path)
