"""Deterministic application of human-approved ClientPC operations. No LLM calls."""
import base64
import contextlib
import hashlib
import json
import os
import pathlib
import re
import sqlite3
import subprocess
import tempfile
import time
import uuid
from datetime import datetime, timezone

from ingest import Ingestor
from knowledge_guard import guard
from site_settings import site_value


def sha(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix='.apply-')
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)


class Rejected(Exception):
    def __init__(self, code, reason, http=422):
        self.code, self.reason, self.http = code, reason, http


def require(condition, code='invalid_schema', reason='Invalid operation schema.', http=422):
    if not condition:
        raise Rejected(code, reason, http)



PROPOSAL_SCHEMA = 'localbrain.decision-proposal.v1'
REVIEW_SCHEMA = 'localbrain.reviewed-decision.v1'
HOST_OWNER = site_value('host_owner', 'ClientPC-windows-host')
CLIENT_RECORD_PREFIX = site_value('client_record_prefix', 'ClientPC-')
APPLICATION_SOURCE = site_value('application_source', 'ClientPC')
HOST_APPROVAL_MODEL = site_value('host_approval_model', 'authenticated-ClientPC-host-attestation-v1')


def safe_id(value):
    return isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{8,80}', value) is not None


def review_hash(operation, payload):
    return sha(encoded({'schema':REVIEW_SCHEMA, 'operation':operation,
                        'payload':{k:v for k,v in payload.items() if k != 'approval'}}))


class ProposalStore:
    """Immutable decision envelope, independently anchored in backend runtime."""
    def __init__(self, brain):
        self.brain = brain
        self.engine = ApplyEngine(brain)

    def paths(self, proposal_id):
        require(safe_id(proposal_id), 'invalid_proposal_id', 'Invalid proposal ID.')
        relative = 'drafts/pending-review/'+proposal_id+'.md'
        return [self.engine.path(relative+suffix, internal=True)
                for suffix in ('', '.meta.json', '.proposal.json')]

    def claim_path(self, proposal_id):
        require(safe_id(proposal_id), 'invalid_proposal_id', 'Invalid proposal ID.')
        return self.engine.runtime_path('proposal-bindings/'+proposal_id+'.json')

    def adoption_path(self, proposal_id):
        require(safe_id(proposal_id), 'invalid_proposal_id', 'Invalid proposal ID.')
        return self.engine.path('log/adoptions/proposal-'+proposal_id+'.json', internal=True)

    def read(self, proposal_id):
        paths = self.paths(proposal_id); claim_path = self.claim_path(proposal_id)
        require(claim_path.is_file(), 'unbound_proposal', 'Proposal is missing or legacy; resubmit for exact human review.', 409)
        require(claim_path.stat().st_size <= 4096, 'proposal_integrity', 'Invalid proposal binding.', 409)
        try:
            claim = json.loads(claim_path.read_bytes())
            require(set(claim) == {'proposal_hash','file_hashes'}, 'proposal_integrity', 'Invalid binding.', 409)
            require(all(p.is_file() and p.stat().st_size <= 400000 for p in paths),
                    'proposal_integrity', 'Missing or oversized proposal files.', 409)
            data = [p.read_bytes() for p in paths]
            require(claim['file_hashes'] == [sha(v) for v in data],
                    'proposal_integrity', 'Proposal files changed after creation.', 409)
            envelope = json.loads(data[2]); meta = json.loads(data[1])
            require(set(envelope) == {'schema','proposal_id','kind','project','title','body','references'}
                    and envelope['schema'] == PROPOSAL_SCHEMA and envelope['proposal_id'] == proposal_id,
                    'proposal_integrity', 'Invalid proposal envelope.', 409)
            require(data[2] == encoded(envelope) and sha(data[2]) == claim['proposal_hash'],
                    'proposal_integrity', 'Noncanonical or replaced proposal envelope.', 409)
            require(envelope['kind'] == 'decision' and meta['project'] == envelope['project']
                    and meta['status'] == 'draft' and meta['proposal_kind'] == 'decision'
                    and meta['proposal_hash'] == claim['proposal_hash'],
                    'proposal_integrity', 'Proposal metadata differs.', 409)
            require(data[0] == ('# '+envelope['title']+'\n\n'+envelope['body']+'\n').encode('utf-8'),
                    'proposal_integrity', 'Proposal Markdown differs.', 409)
        except (ValueError, KeyError, TypeError, UnicodeError):
            raise Rejected('proposal_integrity', 'Invalid saved proposal.', 409)
        relative_paths = [p.relative_to(self.brain.docs).as_posix() for p in paths]
        tracked = self.engine.git('ls-files','--error-unmatch','--',*relative_paths,check=False)
        require(tracked.returncode == 0 and not self.engine.git('status','--porcelain','--',*relative_paths).stdout.strip(),
                'proposal_not_committed','Proposal creation did not complete clean Git persistence.',409)
        # Compare HEAD bytes as well, protecting a Git-index-only replacement.
        require(all(self.engine.git('show','HEAD:'+relative).stdout == raw
                    for relative,raw in zip(relative_paths,data)),
                'proposal_integrity','Proposal Git history differs from anchored files.',409)
        consumption = self.adoption_path(proposal_id)
        anchor = self.engine.runtime_path('proposal-adoptions/'+proposal_id+'.json')
        status = 'draft'
        if anchor.exists():
            require(consumption.is_file(), 'proposal_integrity', 'Committed consumption is missing.', 409)
            try:
                anchor_value = json.loads(anchor.read_bytes())
                require(anchor_value['proposal_hash'] == claim['proposal_hash']
                        and anchor_value['consumption_hash'] == sha(consumption.read_bytes()),
                        'proposal_integrity','Committed consumption was edited.',409)
            except (ValueError,KeyError,TypeError):
                raise Rejected('proposal_integrity','Invalid adoption anchor.',409)
        if consumption.exists():
            require(consumption.is_file() and consumption.stat().st_size <= 16384,
                    'proposal_integrity', 'Invalid consumption record.', 409)
            try:
                adopted = json.loads(consumption.read_bytes())
                require(adopted['proposal_hash'] == claim['proposal_hash'] and adopted['proposal_id'] == proposal_id,
                        'proposal_integrity', 'Consumption record mismatch.', 409)
                status = 'adopted'
            except (ValueError, KeyError, TypeError):
                raise Rejected('proposal_integrity', 'Invalid consumption record.', 409)
        return {'proposal_id':proposal_id, 'proposal_hash':claim['proposal_hash'],
                'schema':PROPOSAL_SCHEMA, 'status':status, 'payload':envelope,
                'content':envelope['body'], 'path':paths[0].relative_to(self.brain.docs).as_posix(),
                'file_hashes':claim['file_hashes']}

    def create(self, request_id, title, body, project, references):
        with guard(self.brain):
            require(safe_id(request_id), 'invalid_proposal_id', 'Invalid proposal ID.')
            require(isinstance(project,str) and re.fullmatch(r'[\w.-]{1,80}',project))
            require(isinstance(title,str) and 0 < len(title.strip()) <= 200 and len(title) <= 200)
            require(isinstance(body,str) and body.strip() and len(body.encode('utf-8')) <= 200000)
            require(isinstance(references,list) and 1 <= len(references) <= 30)
            # Reuse exact source/path/hash/location/provenance and secret validations.
            for ref in references: self.engine.target(ref, project, reference=True)
            value = {'schema':PROPOSAL_SCHEMA, 'proposal_id':request_id, 'kind':'decision',
                     'project':project, 'title':title, 'body':body, 'references':references}
            raw = encoded(value)
            require(not Ingestor(self.brain).contains_secret(raw.decode('utf-8')), 'secret_detected', 'Proposal contains a secret pattern.')
            content_hash = sha(raw); paths = self.paths(request_id)
            claim_path = self.claim_path(request_id)
            if claim_path.exists():
                existing = self.read(request_id)
                require(existing['proposal_hash'] == content_hash, 'proposal_id_conflict', 'Proposal ID has different content.', 409)
                return {**existing, 'duplicate':True}
            require(not any(path.exists() for path in paths), 'legacy_or_partial_proposal',
                    'Existing legacy/partial proposal is preserved; use a new proposal ID.', 409)
            meta = {'project':project,'status':'draft','evidence_level':'inferred','knowledge_type':'DECISION',
                    'proposal_kind':'decision','review_required':True,'created_at':datetime.now(timezone.utc).isoformat(),
                    'source_references':references,'proposal_hash':content_hash,'proposal_schema':PROPOSAL_SCHEMA}
            data = [('# '+title+'\n\n'+body+'\n').encode('utf-8'), encoded(meta), raw]
            # A partial creation fails closed; no overwrite/migration/automatic adoption.
            for path, value_bytes in zip(paths,data): atomic(path,value_bytes)
            atomic(claim_path,encoded({'proposal_hash':content_hash,'file_hashes':[sha(v) for v in data]}))
            self.brain.git_commit(paths, 'brain: bound decision proposal '+request_id)
            return {**self.read(request_id), 'duplicate':False}


class ApplyEngine:
    def __init__(self, brain):
        self.brain = brain
        self.docs = brain.docs
        self.base = self.runtime_path('apply-transactions')
        self.receipts = self.runtime_path('apply-results')


    def runtime_path(self, relative):
        """Read/write/recovery use the same canonical, non-reparse runtime path."""
        parts = relative.split('/')
        require(parts[0] in ('proposal-bindings','proposal-adoptions','approval-consumptions',
                            'apply-transactions','apply-results','apply-claims'))
        require(all(p not in ('','.','..') and re.fullmatch(r'[A-Za-z0-9_.-]{1,100}',p) for p in parts),
                'invalid_path','Invalid runtime path.')
        path = self.brain.runtime
        require(not path.is_symlink() and not getattr(path,'is_junction',lambda:False)()
                and path.resolve().is_relative_to(self.brain.root.resolve()),
                'invalid_path','Linked or external runtime root.')
        for part in parts:
            if path.is_dir():
                names = {entry.name.casefold():entry.name for entry in path.iterdir()}
                require(names.get(part.casefold(),part) == part,'invalid_path','Noncanonical runtime casing.')
            path = path/part
            require(not path.is_symlink() and not getattr(path,'is_junction',lambda:False)(),
                    'invalid_path','Linked runtime component.')
        require(path.resolve().is_relative_to(self.brain.runtime.resolve()),'invalid_path','External runtime path.')
        return path

    def git(self, *args, env=None, data=None, check=True):
        result = subprocess.run(['git', '-c', f'safe.directory={self.docs}', '-C', str(self.docs), *args],
                                input=data, capture_output=True, env=env)
        if check and result.returncode:
            raise RuntimeError('Knowledge Git operation failed')
        return result

    def path(self, relative, internal=False):
        require(isinstance(relative, str) and 0 < len(relative) < 240, 'invalid_path', 'Invalid document path.')
        parts = relative.split('/')
        require(not any(p in ('', '.', '..') or p[-1:] in (' ', '.') or
                        re.search(r'[\\:\x00-\x1f<>"|?*]', p) or
                        re.match(r'(?i)^(con|prn|aux|nul|com[0-9]|lpt[0-9])(?:\.|$)', p)
                        for p in parts), 'invalid_path', 'Only canonical relative paths are accepted.')
        roots = ('raw', 'wiki', 'decisions', 'incidents', 'comparisons', 'projects', 'benchmarks')
        require((parts[0] in roots and relative.endswith('.md')) or
                (internal and parts[0] in ('drafts','log') and relative.endswith(('.md','.json'))), 'invalid_path', 'Document is outside approved knowledge folders.')
        path = self.docs
        for part in parts:
            if path.is_dir():
                names = {entry.name.casefold():entry.name for entry in path.iterdir()}
                require(names.get(part.casefold(), part) == part, 'invalid_path', 'Use the exact canonical path casing.')
            path = path / part
            require(not path.is_symlink() and not getattr(path, 'is_junction', lambda: False)(),
                    'invalid_path', 'Linked paths are not accepted.')
        require(path.resolve().is_relative_to(self.docs.resolve()), 'invalid_path', 'Path escapes knowledge root.')
        return path

    def read(self, relative):
        path = self.path(relative)
        side = path.with_suffix('.md.meta.json')
        require(path.is_file() and side.is_file(), 'missing_target', 'Document or metadata is missing.')
        require(not side.is_symlink() and not getattr(side, 'is_junction', lambda: False)(),
                'invalid_path', 'Linked metadata is not accepted.')
        require(path.stat().st_size <= 4*1024*1024 and side.stat().st_size <= 4*1024*1024,
                'invalid_target', 'Document is too large.')
        raw, meta_raw = path.read_bytes(), side.read_bytes()
        try:
            content = raw.decode('utf-8-sig')
            meta = json.loads(meta_raw)
        except (ValueError, UnicodeError):
            raise Rejected('invalid_target', 'Document encoding or metadata is invalid.')
        require(isinstance(meta, dict), 'invalid_target', 'Metadata must be an object.')
        if relative.startswith('raw/') and meta.get('source_hash'):
            require(meta['source_hash'] == sha(raw), 'provenance_mismatch', 'Raw source differs from its recorded provenance.', 409)
        require(not Ingestor(self.brain).contains_secret(content + '\n' + meta_raw.decode('utf-8-sig')),
                'secret_detected', 'Document contains a high-confidence secret pattern.')
        return {'path':relative, 'content':content, 'metadata':meta,
                'expected_hash':sha(raw), 'expected_metadata_hash':sha(meta_raw)}

    def snapshot(self, path):
        with guard(self.brain):
            return self.read(path)

    def target(self, value, project, reference=False):
        keys = {'path', 'expected_hash', 'expected_metadata_hash'} | ({'relevant_location'} if reference else set())
        require(isinstance(value, dict) and set(value) == keys)
        require(all(isinstance(value[k], str) and re.fullmatch('[0-9a-f]{64}', value[k])
                    for k in ('expected_hash', 'expected_metadata_hash')))
        record = self.read(value['path'])
        require(all(record[k] == value[k] for k in ('expected_hash', 'expected_metadata_hash')),
                'stale_target', 'Target changed after approval.', 409)
        require(record['metadata'].get('project') == project, 'project_mismatch', 'All documents must belong to the same project.')
        if reference:
            require(record['metadata'].get('status') == 'active', 'invalid_reference', 'Reference must be active.')
            require(isinstance(value['relevant_location'], str), 'invalid_reference', 'Reference location must be text.')
            match = re.fullmatch(r'L([1-9][0-9]*)(?:-L?([1-9][0-9]*))?', value['relevant_location'])
            require(match is not None, 'invalid_reference', 'Reference location must be L1 or L1-L5.')
            first, last = int(match[1]), int(match[2] or match[1])
            require(first <= last <= len(record['content'].splitlines()), 'invalid_reference', 'Reference location is outside the source.')
        else:
            require(value['path'].split('/')[0] in ('wiki', 'decisions'), 'invalid_target', 'Only wiki and decision records can be replaced.')
        return record

    def schema(self, operation, payload):
        require(operation in ('decision', 'human-decision', 'supersede', 'merge'))
        required = {'request_id', 'project'}
        required |= {'content', 'title', 'references'} if operation in ('decision', 'human-decision', 'merge') else {'old', 'new'}
        if operation == 'merge': required |= {'sources'}
        if operation in ('decision','human-decision'): required |= {'approval'}
        if operation == 'decision': required |= {'origin_proposal_id','expected_proposal_hash'}
        require(isinstance(payload, dict) and set(payload) == required)
        require(isinstance(payload['request_id'], str) and re.fullmatch(r'[A-Za-z0-9_-]{8,80}', payload['request_id']))
        require(isinstance(payload['project'], str) and re.fullmatch(r'[\w.-]{1,80}', payload['project']))
        if operation in ('decision', 'human-decision', 'merge'):
            require(isinstance(payload['title'], str) and 0 < len(payload['title'].strip()) <= 200)
            require(isinstance(payload['content'], str) and 0 < len(payload['content'].strip()) and len(payload['content'].encode()) <= 200000)
            require(isinstance(payload['references'], list) and 1 <= len(payload['references']) <= 30)
        require(not Ingestor(self.brain).contains_secret(encoded(payload).decode()), 'secret_detected', 'Request contains a high-confidence secret pattern.')


    def host_approval(self, operation, payload, client):
        require(client.get('source') == 'mTLS Gateway / trusted Continue approval'
                and client.get('address') == site_value('client_address', 'localbrain-client', self.brain.root)
                and isinstance(client.get('certificate_sha256'),str)
                and re.fullmatch(r'[0-9a-f]{64}',client['certificate_sha256'])
                and client.get('approval_owner') == HOST_OWNER,
                'host_attestation_required', 'ClientPC authenticated Host attestation is required.', 403)
        approval = payload['approval']
        keys = {'version','owner','approval_id','task_id','request_id','operation',
                'proposal_id','proposal_hash','decision_hash','issued_at','expires_at','provenance'}
        require(isinstance(approval,dict) and set(approval) == keys)
        require(type(approval['version']) is int and approval['version'] == 1 and approval['owner'] == HOST_OWNER)
        require(all(safe_id(approval[k]) for k in ('approval_id','task_id','request_id')))
        require(approval['request_id'] == payload['request_id'] and approval['operation'] == operation)
        require(approval['decision_hash'] == review_hash(operation,payload),
                'approval_mismatch', 'Host reviewed content differs.', 409)
        require(all(type(approval[k]) is int for k in ('issued_at','expires_at')))
        now = int(time.time())
        require(0 <= approval['issued_at'] <= now and now < approval['expires_at']
                and 0 < approval['expires_at']-approval['issued_at'] <= 3600,
                'approval_expired', 'Approval is expired, future-dated or exceeds one hour.', 409)
        if operation == 'decision':
            require(safe_id(payload['origin_proposal_id'])
                    and isinstance(payload['expected_proposal_hash'],str)
                    and re.fullmatch(r'[0-9a-f]{64}',payload['expected_proposal_hash']))
            require(approval['proposal_id'] == payload['origin_proposal_id']
                    and approval['proposal_hash'] == payload['expected_proposal_hash']
                    and approval['provenance'] == 'human-reviewed-model-proposal',
                    'approval_mismatch', 'Host approval is not bound to the proposal.', 409)
        else:
            require(approval['proposal_id'] is None and approval['proposal_hash'] is None
                    and approval['provenance'] == 'explicit-human-owned-decision')
        approval_path = self.path('log/approvals/'+approval['approval_id']+'.json',internal=True)
        used = self.runtime_path('approval-consumptions/'+approval['approval_id']+'.json')
        require(not approval_path.exists() and not used.exists(), 'approval_used', 'Host approval already consumed.', 409)
        return approval, approval_path

    def cycle(self, updates):
        edges = {}
        for folder in ('wiki', 'decisions'):
            for side in (self.docs / folder).rglob('*.md.meta.json'):
                relative = side.relative_to(self.docs).as_posix().removesuffix('.meta.json')
                meta = self.read(relative)['metadata']
                targets = [meta[k] for k in ('superseded_by', 'merged_into') if meta.get(k)]
                require(all(isinstance(t, str) for t in targets), 'invalid_state', 'Invalid replacement link.')
                edges[relative] = targets
        edges.update(updates)
        visited, active = set(), set()
        def visit(node):
            require(node not in active, 'cycle', 'Replacement graph contains a cycle.', 409)
            if node in visited: return
            active.add(node)
            for target in edges.get(node, []): visit(target)
            active.remove(node); visited.add(node)
        for node in edges: visit(node)

    def plan(self, operation, payload, audit_id, client):
        self.schema(operation, payload)
        project = payload['project']; records = []; references = []; writes = {}
        approval = None; proposal = None
        if operation in ('decision','human-decision'):
            approval, approval_path = self.host_approval(operation,payload,client)
            if operation == 'decision':
                proposal = ProposalStore(self.brain).read(payload['origin_proposal_id'])
                require(proposal['proposal_hash'] == payload['expected_proposal_hash'],
                        'proposal_hash_mismatch', 'Saved proposal differs from reviewed hash.', 409)
                require(proposal['status'] == 'draft', 'proposal_already_adopted', 'Proposal already adopted.', 409)
                value = proposal['payload']
                require(value['kind'] == 'decision' and value['project'] == project
                        and value['title'] == payload['title'] and value['body'] == payload['content']
                        and encoded(value['references']) == encoded(payload['references']),
                        'proposal_content_mismatch', 'Apply fields differ from the saved reviewed proposal.', 409)
        stamp = datetime.now(timezone.utc).isoformat()
        for ref in payload.get('references', []):
            record = self.target(ref, project, reference=True)
            references.append({**ref, 'source_id':record['metadata'].get('source_id', 'source:'+record['expected_hash']),
                'source_path':ref['path'], 'source_hash':record['expected_hash'],
                'source_type':record['metadata'].get('source_type', 'knowledge'),
                'source_date':record['metadata'].get('source_date'), 'retrieved_at':stamp,
                'knowledge_type':record['metadata'].get('knowledge_type', 'SOURCE'), 'confidence':'human-approved'})
        if operation == 'supersede':
            old = self.target(payload['old'], project); new = self.target(payload['new'], project)
            require(old['path'].casefold() != new['path'].casefold(), 'same_target', 'Old and new must differ.')
            records = [old, new]; destination = new['path']; sources = [old]
        elif operation == 'merge':
            require(isinstance(payload['sources'], list) and 2 <= len(payload['sources']) <= 20)
            records = [self.target(v, project) for v in payload['sources']]
            require(len({v['path'].casefold() for v in records}) == len(records), 'same_source', 'Merge sources must be distinct.')
            sources = records; destination = 'wiki/synthesis/'+CLIENT_RECORD_PREFIX+payload['request_id']+'.md'
        else:
            sources = []; destination = 'decisions/'+CLIENT_RECORD_PREFIX+payload['request_id']+'.md'
        self.cycle({r['path']:[destination] for r in sources})
        for record in records:
            require(not record['metadata'].get('merged_into'), 'already_merged', 'Target has already been merged.', 409)
            require(record['metadata'].get('status') == 'active' and not record['metadata'].get('superseded_by'),
                    'invalid_state', 'Targets must be active and unreplaced.', 409)
        if operation != 'supersede':
            target = self.path(destination)
            require(not target.exists() and not target.with_suffix('.md.meta.json').exists(), 'destination_exists', 'Output already exists.', 409)
            # A different request ID cannot silently duplicate the same final document.
            content_hash = sha(payload['content'].encode())
            for folder in ('decisions', 'wiki'):
                for candidate in (self.docs / folder).rglob('*.md'):
                    if sha(candidate.read_bytes()) == content_hash:
                        raise Rejected('duplicate_content', 'Identical final content already exists.', 409)
            metadata = {'project':project, 'status':'active', 'knowledge_type':'DECISION' if operation in ('decision','human-decision') else 'SYNTHESIS',
                'evidence_level':'inferred', 'title':payload['title'], 'source_references':references,
                'applied_from':APPLICATION_SOURCE, 'approval_model':'trusted-continue-ask-first', 'audit_id':audit_id, 'created_at':stamp}
            if approval:
                metadata.update(approval_model=HOST_APPROVAL_MODEL,
                                host_approval=approval,
                                human_identity_verification='not independently verified by ServerPC')
                if proposal:
                    metadata.update(origin_proposal_id=proposal['proposal_id'],
                                    expected_proposal_hash=proposal['proposal_hash'])
            if operation == 'merge':
                metadata['merged_from'] = [{k:r[k] for k in ('path', 'expected_hash', 'expected_metadata_hash')} for r in sources]
                metadata['inherited_provenance'] = [{'path':r['path'], 'metadata':r['metadata']} for r in sources]
            writes[destination] = payload['content'].encode('utf-8')
            writes[destination+'.meta.json'] = encoded(metadata)
        for source in sources:
            metadata = dict(source['metadata'])
            metadata.update(status='superseded', updated_at=stamp, last_apply_audit=audit_id)
            metadata['superseded_by' if operation=='supersede' else 'merged_into'] = destination
            writes[source['path']+'.meta.json'] = encoded(metadata)
        audit = {'audit_id':audit_id, 'request_id':payload['request_id'], 'timestamp':stamp, 'operation':operation,
                 'authenticated_client':client, 'approval_model':'trusted-continue-ask-first',
                 'targets':[{k:r[k] for k in ('path', 'expected_hash', 'expected_metadata_hash')} for r in records],
                 'references':references, 'pre_state':{r['path']:r['metadata'] for r in records},
                 'post_state':{p.removesuffix('.meta.json'):json.loads(v) for p,v in writes.items() if p.endswith('.meta.json')},
                 'result':'applied', 'reason_code':None}
        if approval:
            consumption = {'approval':approval, 'request_id':payload['request_id'],
                           'audit_id':audit_id, 'destination':destination, 'decision_hash':approval['decision_hash'],
                           'client_certificate_sha256':client['certificate_sha256']}
            writes[approval_path.relative_to(self.docs).as_posix()] = encoded(consumption)
            if proposal:
                consumed = {**consumption,'proposal_id':proposal['proposal_id'],'proposal_hash':proposal['proposal_hash']}
                path = ProposalStore(self.brain).adoption_path(proposal['proposal_id'])
                writes[path.relative_to(self.docs).as_posix()] = encoded(consumed)
            audit.update(approval_model=HOST_APPROVAL_MODEL, host_approval=approval,
                         human_identity_verification='not independently verified by ServerPC',
                         origin_proposal_id=proposal['proposal_id'] if proposal else None)
        writes['log/apply/'+audit_id+'.json'] = encoded(audit)
        return writes, destination, audit

    def receipt(self, request_id):
        return self.runtime_path('apply-results/'+request_id+'.json')

    def save_result(self, request_id, payload_hash, result, http):
        receipt_path = self.receipt(request_id); used = None; adopted = None
        if result.get('status') == 'applied' and result.get('approval_id'):
            used = self.runtime_path('approval-consumptions/'+result['approval_id']+'.json')
            if result.get('origin_proposal_id'):
                adopted = self.runtime_path('proposal-adoptions/'+result['origin_proposal_id']+'.json')
        atomic(receipt_path, encoded({'payload_hash':payload_hash, 'result':result, 'http':http}))
        if used:
            atomic(used, encoded({'request_id':request_id,'payload_hash':payload_hash,'approval_id':result['approval_id']}))
        if adopted:
            atomic(adopted, encoded({'proposal_hash':result['expected_proposal_hash'],
                                    'consumption_hash':result['consumption_hash'],'request_id':request_id}))

    def database_snapshot(self, directory):
        databases = ['index.sqlite3', 'semantic.sqlite3']
        for name in databases:
            source = self.brain.runtime / name
            if source.exists():
                with contextlib.closing(sqlite3.connect(source)) as a, contextlib.closing(sqlite3.connect(directory / name)) as b:
                    a.backup(b)
        manifest = self.brain.runtime / 'index-manifest.json'
        return {'databases':[n for n in databases if (directory/n).exists()],
                'manifest':base64.b64encode(manifest.read_bytes()).decode() if manifest.exists() else None}

    def restore(self, directory, journal):
        for path, old in journal['before'].items():
            target = self.docs / path
            if old is None:
                target.unlink(missing_ok=True)
            else:
                atomic(target, base64.b64decode(old))
        for name in journal['database']['databases']:
            with contextlib.closing(sqlite3.connect(directory/name)) as a, contextlib.closing(sqlite3.connect(self.brain.runtime/name)) as b:
                a.backup(b)
        manifest = self.brain.runtime/'index-manifest.json'
        if journal['database']['manifest'] is None:
            manifest.unlink(missing_ok=True)
        else:
            atomic(manifest, base64.b64decode(journal['database']['manifest']))
        self.brain.semantic.error = None

    def committed(self, journal):
        commit = journal.get('commit')
        return bool(commit and self.git('merge-base', '--is-ancestor', commit, 'HEAD', check=False).returncode == 0)

    def cleanup_snapshots(self, directory):
        # Exact temporary snapshot files only; retained journal + Git support revert/reindex.
        for name in ('index.sqlite3', 'semantic.sqlite3', 'git-index'):
            try: (directory/name).unlink(missing_ok=True)
            except OSError: pass

    def recover(self):
        base = self.runtime_path('apply-transactions')
        if not base.exists(): return
        for journal_path in base.glob('*/journal.json'):
            journal_path = self.runtime_path('apply-transactions/'+journal_path.parent.name+'/journal.json')
            journal = json.loads(journal_path.read_bytes())
            if journal.get('finished'):
                self.cleanup_snapshots(journal_path.parent)
                continue
            if self.committed(journal):
                self.git('reset', '-q', 'HEAD', '--', *journal['before'].keys())
                self.save_result(journal['request_id'], journal['payload_hash'], journal['success'], 200)
            else:
                self.restore(journal_path.parent, journal)
            journal['finished'] = True
            atomic(journal_path, encoded(journal))
            self.cleanup_snapshots(journal_path.parent)

    def publish(self, directory, journal, writes):
        env = dict(os.environ, GIT_INDEX_FILE=str((directory/'git-index').resolve()))
        self.git('read-tree', journal['parent'], env=env)
        self.git('add', '--', *writes.keys(), env=env)
        tree = self.git('write-tree', env=env).stdout.decode().strip()
        commit = self.git('commit-tree', tree, '-p', journal['parent'], '-m',
                          'brain: apply '+journal['operation']+' '+journal['request_id']).stdout.decode().strip()
        journal['commit'] = commit
        journal['success']['git_commit'] = commit
        atomic(directory/'journal.json', encoded(journal))
        # Compare-and-swap is the sole commit point. An uncertain result is checked by recover().
        self.git('update-ref', 'HEAD', commit, journal['parent'])

    def apply(self, operation, payload, client):
        request_id = payload.get('request_id') if isinstance(payload, dict) else None
        if not isinstance(request_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,80}', request_id):
            return 400, {'status':'rejected', 'reason_code':'invalid_schema', 'reason':'Invalid request_id.', 'request_id':None}
        try:
            if operation in ('decision','human-decision'):
                require('approval' in payload and (operation != 'decision' or
                        {'origin_proposal_id','expected_proposal_hash'} <= set(payload)),
                        'proposal_binding_required', 'Legacy decision apply is disabled; use bound or explicit-human Host contract.', 409)
                require(client.get('source') == 'mTLS Gateway / trusted Continue approval'
                        and client.get('address') == site_value('client_address', 'localbrain-client', self.brain.root)
                        and isinstance(client.get('certificate_sha256'),str)
                        and re.fullmatch(r'[0-9a-f]{64}',client['certificate_sha256'])
                        and client.get('approval_owner') == HOST_OWNER,
                        'host_attestation_required', 'Authenticated ClientPC Host is required.', 403)
                self.schema(operation,payload)
            payload_hash = sha(encoded({'operation':operation, 'payload':payload}))
            with guard(self.brain):
                claim = self.runtime_path('apply-claims/'+request_id+'.json')
                if claim.exists():
                    require(json.loads(claim.read_bytes())['payload_hash'] == payload_hash,
                            'request_id_conflict', 'Request ID was already used with a different payload.', 409)
                else:
                    atomic(claim, encoded({'payload_hash':payload_hash}))
                saved = self.receipt(request_id)
                if saved.exists():
                    prior = json.loads(saved.read_bytes())
                    require(prior['payload_hash'] == payload_hash, 'request_id_conflict', 'Request ID was already used with a different payload.', 409)
                    if operation in ('decision','human-decision') and prior['result'].get('status') == 'applied':
                        require(prior['result'].get('client_certificate_sha256') == client['certificate_sha256'],
                                'host_attestation_mismatch','Retry must use the original authenticated Host.',403)
                    return prior['http'], prior['result']
                audit_id = str(uuid.uuid4())
                try:
                    writes, destination, audit = self.plan(operation, payload, audit_id, client)
                except Rejected as exc:
                    result = {'status':'rejected', 'reason_code':exc.code, 'reason':exc.reason, 'request_id':request_id, 'audit_id':audit_id}
                    self.save_result(request_id, payload_hash, result, exc.http)
                    atomic(self.brain.runtime/'apply-audit'/f'{audit_id}.json', encoded({**result, 'operation':operation,
                        'authenticated_client':client, 'timestamp':datetime.now(timezone.utc).isoformat(), 'payload_hash':payload_hash}))
                    return exc.http, result
                directory = self.runtime_path('apply-transactions/'+request_id)
                directory.mkdir(parents=True, exist_ok=True)
                require(not self.git('status', '--porcelain', '--', *writes.keys()).stdout.strip(),
                        'dirty_target', 'Affected paths have uncommitted changes.', 409)
                parent = self.git('rev-parse', 'HEAD').stdout.decode().strip()
                # Source contents are never rewritten. Only these exact affected files are snapshotted.
                before = {p:base64.b64encode((self.docs/p).read_bytes()).decode() if (self.docs/p).exists() else None for p in writes}
                journal = {'request_id':request_id, 'payload_hash':payload_hash, 'parent':parent, 'operation':operation,
                           'before':before, 'database':self.database_snapshot(directory), 'finished':False,
                           'success':{'status':'applied', 'operation':operation, 'request_id':request_id, 'audit_id':audit_id,
                                      'paths':[p for p in writes if p.endswith('.md') or p.endswith('.md.meta.json')], 'destination':destination}}
                if operation in ('decision','human-decision'):
                    journal['success'].update(approval_id=payload['approval']['approval_id'],
                                              task_id=payload['approval']['task_id'],
                                              client_certificate_sha256=client['certificate_sha256'])
                    if operation == 'decision':
                        consumption_path = 'log/adoptions/proposal-'+payload['origin_proposal_id']+'.json'
                        journal['success'].update(origin_proposal_id=payload['origin_proposal_id'],
                                                  expected_proposal_hash=payload['expected_proposal_hash'],
                                                  consumption_hash=sha(writes[consumption_path]))
                atomic(directory/'journal.json', encoded(journal))
                try:
                    for path, data in writes.items(): atomic(self.docs/path, data)
                    indexed = self.brain.index()
                    if indexed['errors'] or indexed.get('semantic', {}).get('degraded'):
                        raise RuntimeError('Index not fully available')
                    self.publish(directory, journal, writes)
                except Exception:
                    if not self.committed(journal):
                        self.restore(directory, journal)
                        journal['finished'] = True
                        atomic(directory/'journal.json', encoded(journal))
                        self.cleanup_snapshots(directory)
                        raise
                result = journal['success']
                # A post-commit receipt I/O failure cannot turn committed success into an error.
                try:
                    self.git('reset', '-q', 'HEAD', '--', *journal['before'].keys())
                    self.save_result(request_id, payload_hash, result, 200)
                    journal['finished'] = True
                    atomic(directory/'journal.json', encoded(journal))
                    self.cleanup_snapshots(directory)
                except (OSError, RuntimeError):
                    pass  # Durable pre-publication journal reconstructs result on the next request.
                return 200, result
        except Rejected as exc:
            return exc.http, {'status':'rejected', 'reason_code':exc.code, 'reason':exc.reason, 'request_id':request_id}
        except TimeoutError:
            return 409, {'status':'rejected', 'reason_code':'busy', 'reason':'Knowledge writer is busy; retry the same request.', 'request_id':request_id}
        except Exception:
            try:
                audit_id = str(uuid.uuid4())
                atomic(self.brain.runtime/'apply-audit'/f'{audit_id}.json', encoded({
                    'audit_id':audit_id, 'request_id':request_id, 'operation':operation,
                    'timestamp':datetime.now(timezone.utc).isoformat(), 'authenticated_client':client,
                    'result':'error', 'reason_code':'apply_failed'}))
            except OSError:
                pass  # Durable transaction journal remains the recovery authority.
            return 500, {'status':'error', 'reason_code':'apply_failed', 'reason':'Apply failed; recovery is required before further knowledge access.', 'request_id':request_id}
