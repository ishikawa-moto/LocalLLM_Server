"""Explicit local import of a Host-prepared PDF reference; never fact adoption."""
from __future__ import annotations
import datetime,hashlib,json,os,pathlib,shutil,tempfile
from knowledge_guard import guarded
from knowledge_pipeline import atomic_json
from ingest import Ingestor

MAX_BYTES=4*1024*1024
def sha(raw):return hashlib.sha256(raw).hexdigest()

def read_bundle(bundle,text_name='extracted.md'):
    bundle=pathlib.Path(bundle).absolute()
    if bundle.is_symlink():raise ValueError('Linked reference bundle')
    def read(name,limit):
        path=bundle/name
        if path.is_symlink():raise ValueError('Linked reference payload')
        with path.open('rb') as stream:
            raw=stream.read(limit+1)
        if not raw or len(raw)>limit:raise ValueError('Reference input exceeds bound')
        return raw
    original=read('original.pdf',MAX_BYTES);text=read(text_name,MAX_BYTES);manifest_bytes=read('manifest.json',1_000_000)
    m=json.loads(manifest_bytes);text.decode('utf-8')
    if not original.startswith(b'%PDF-') or m.get('schema')!='localbrain.external-reference.v1' or m.get('source_type')!='pdf':raise ValueError('Invalid PDF reference')
    if (m.get('review_state')!='unverified' or m.get('allow_fact_promotion') is not False or m.get('explicit_claim_verification_required') is not True
        or any(m.get(k) is not False for k in ('claims_are_verified','knowledge_adopted','current_repository_authority'))):raise ValueError('Reference cannot grant truth/adoption')
    original_hash=sha(original);text_hash=sha(text)
    if m.get('original_pdf_sha256')!=original_hash or m.get('source_id')!='source:'+original_hash or m.get('source_hash')!=text_hash or m.get('extracted_text_sha256')!=text_hash:raise ValueError('Reference identity/hash differs')
    if m.get('independent_document_count')!=1:raise ValueError('Pages are one document')
    if not isinstance(m.get('source_uri'),str) or not m['source_uri'].strip() or len(m['source_uri'])>2000:raise ValueError('Source URI required')
    retrieved=datetime.datetime.fromisoformat(m.get('retrieved_at','').replace('Z','+00:00'))
    if retrieved.tzinfo is None:raise ValueError('Explicit retrieval timezone required')
    pages=m.get('page_map');end=0
    if not isinstance(pages,list) or not 1<=len(pages)<=200:raise ValueError('Invalid page map')
    for number,page in enumerate(pages,1):
        next_end=page.get('end_byte')
        if (page.get('page')!=number or page.get('start_byte')!=end or not isinstance(next_end,int) or not end<next_end<=len(text)
            or page.get('text_sha256')!=sha(text[end:next_end])):raise ValueError('Page binding differs')
        end=next_end
    if end!=len(text):raise ValueError('Incomplete page map')
    return original,text,manifest_bytes,m

@guarded
def import_reference(brain,bundle,project='general'):
    original,text,manifest_bytes,m=read_bundle(bundle)
    if Ingestor(brain).contains_secret(text.decode('utf-8')):raise ValueError('Protected reference text is ineligible')
    original_hash=sha(original);text_hash=sha(text)
    directory=brain.safe_path('raw/external/'+original_hash)
    path=directory/'reference.md';side=directory/'reference.md.meta.json'
    duplicate=directory.exists()
    if duplicate:
        saved=json.loads(side.read_text(encoding='utf-8'))
        retained_original,retained_text,retained_manifest,retained_metadata=read_bundle(directory,'reference.md')
        if ((directory/'original.pdf').is_symlink() or path.is_symlink() or side.is_symlink()
            or sha(retained_original)!=original_hash or retained_text!=text
            or saved.get('source_hash')!=text_hash or saved.get('source_id')!=m['source_id'] or saved.get('allow_fact_promotion') is not False
            or saved.get('evidence_level')!='unknown' or saved.get('review_state')!='unverified'
            or saved.get('page_manifest_hash')!=sha(retained_manifest) or saved.get('original_pdf_sha256')!=original_hash
            or saved.get('retrieved_at')!=retained_metadata['retrieved_at'] or saved.get('source_path')!=retained_metadata['source_uri']):raise ValueError('Existing reference requires explicit repair/extraction revision')
        # Preserve the first original-document provenance even if another URL cites identical bytes.
    else:
        directory.parent.mkdir(parents=True,exist_ok=True)
        stage=pathlib.Path(tempfile.mkdtemp(prefix='external-ref-',dir=brain.runtime))
        try:
            metadata={'project':project,'status':'active','evidence_level':'unknown','review_state':'unverified','source_id':m['source_id'],
                'source_type':'pdf_derived_text','source_path':m['source_uri'],'source_date':m['retrieved_at'],
                'retrieved_at':m['retrieved_at'],'source_hash':text_hash,'original_pdf_sha256':original_hash,
                'original_source_id':m['source_id'],'page_manifest_hash':sha(manifest_bytes),'independent_document_count':1,
                'allow_fact_promotion':False,'claims_are_verified':False,'knowledge_adopted':False,'explicit_claim_verification_required':True}
            for name,raw in [('original.pdf',original),('reference.md',text),('manifest.json',manifest_bytes)]:
                with (stage/name).open('xb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
            atomic_json(stage/'reference.md.meta.json',metadata)
            os.replace(stage,directory)
        finally:
            if stage.exists():
                if not stage.resolve().is_relative_to(brain.runtime.resolve()) or stage.is_symlink():raise ValueError('Unexpected staging cleanup target')
                shutil.rmtree(stage)
    relative=path.relative_to(brain.docs).as_posix()
    log=brain.pipeline.log('external_reference_duplicate' if duplicate else 'external_reference_imported',source_path=relative,source_hash=text_hash,original_pdf_sha256=original_hash,claims_verified=False,knowledge_adopted=False)
    committed=brain.git_commit([path,side,directory/'original.pdf',directory/'manifest.json',log],f'brain: retain review-only PDF reference {original_hash[:12]}')
    if not committed.get('committed'):raise ValueError('Reference durable commit incomplete; retry same import')
    queued=brain.pipeline.enqueue(relative)
    return {'status':'reference-retained-review-required','path':relative,'source_id':m['source_id'],'source_hash':text_hash,
        'original_pdf_sha256':original_hash,'duplicate':duplicate,'queued':queued['queued'],'claims_verified':False,'knowledge_adopted':False}


@guarded
def import_learning(brain, bundle, expected_manifest_sha256):
    """Admin local CLI for an exact Host-pinned packet; creates a normal review draft only."""
    import re
    bundle = pathlib.Path(bundle).absolute()
    if not re.fullmatch(r'[a-f0-9]{64}', expected_manifest_sha256):
        raise ValueError('Exact Host manifest SHA256 is required')
    for p in (bundle, *bundle.parents):
        if p.is_symlink() or p.is_junction():
            raise ValueError('Linked learning packet')
    manifest_path = bundle / 'learning-manifest.json'
    if manifest_path.is_symlink() or not manifest_path.is_file() or not 0 < manifest_path.stat().st_size <= 100_000:
        raise ValueError('Bounded learning manifest required')
    raw = manifest_path.read_bytes()
    if sha(raw) != expected_manifest_sha256:
        raise ValueError('Packet differs from Host-pinned manifest')
    m = json.loads(raw)
    if (m.get('schema_version') != 1 or m.get('original_document_claims_verified') is not False
        or m.get('knowledge_adopted') is not False or m.get('reviewer_required') is not True
        or m.get('adoption_status') != 'LOCAL_CANDIDATE_NOT_DISPATCHED'):
        raise ValueError('Packet cannot grant document truth or adoption')
    sources = m.get('sources')
    if not isinstance(sources, list) or not 1 <= len(sources) <= 4:
        raise ValueError('Bounded explicit observation sources required')
    if (not isinstance(m.get('request_id'), str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,80}', m['request_id'])
        or not isinstance(m.get('title'), str) or not 0 < len(m['title']) <= 200
        or any(c in m['title'] for c in '\r\n')
        or not isinstance(m.get('project'), str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', m['project'])):
        raise ValueError('Invalid writeback identity')
    names=[]; contents=[]
    for source in sources:
        name=source.get('filename')
        if not isinstance(name,str) or not re.fullmatch(r'observation-[a-f0-9]{24}\.md',name):
            raise ValueError('Invalid observation filename')
        p=bundle/name
        if p.is_symlink() or not p.is_file() or not 0 < p.stat().st_size <= 16_000:
            raise ValueError('Bounded observation source required')
        content=p.read_bytes()
        if content.decode('utf-8') != source.get('content') or sha(content) != source.get('content_sha256'):
            raise ValueError('Observed source bytes differ')
        if not isinstance(source.get('original_source_id'),str) or not re.fullmatch(r'source:[a-f0-9]{64}',source['original_source_id']):
            raise ValueError('Original PDF identity required')
        for key in ('repository_hash','diff_hash'):
            if not isinstance(source.get(key),str) or not re.fullmatch(r'[a-f0-9]{64}',source[key]):
                raise ValueError('Repository observation hashes required')
        if source.get('source_event_id') not in [i.removeprefix('event:') for i in m.get('constituent_source_ids',[]) if isinstance(i,str) and i.startswith('event:')]:
            raise ValueError('Canonical event constituent missing')
        if not isinstance(source.get('repository_path'),str) or not source['repository_path'] or not isinstance(source.get('head'),str):
            raise ValueError('Historical repository scope required')
        if Ingestor(brain).contains_secret(content.decode('utf-8')):
            raise ValueError('Protected observation text')
        names.append(name);contents.append(content)
    if (len(set(names)) != len(names) or len({s['repository_path'].casefold() for s in sources}) != len(sources)
        or len({s['repository_hash'] for s in sources}) != len(sources)):
        raise ValueError('Duplicate file/content cannot inflate corroboration')
    if sorted(p.name for p in bundle.iterdir()) != sorted(names+['learning-manifest.json']):
        raise ValueError('Learning packet has extra or missing files')
    body='\n\n'.join(content.decode('utf-8').rstrip('\n') for content in contents)
    if m.get('body') != body or Ingestor(brain).contains_secret(body):
        raise ValueError('Exact historical body differs')
    paths=['raw/'+pathlib.Path(name).stem[:80]+'-'+sha(content)[:12]+'.md' for name,content in zip(names,contents)]
    expected_text=f"# {m['title']}\n\n{body}\n"
    proposal={'title':m['title'],'body':body,'project':m['project'],'references':paths,'kind':'writeback'}
    request_hash=sha(json.dumps(proposal,ensure_ascii=False,indent=2).encode('utf-8'))
    draft=brain.docs/'drafts'/'writeback'/f"{m['request_id']}.md"
    def check_existing_draft():
        side=draft.with_suffix('.md.meta.json')
        if draft.exists() and draft.read_text(encoding='utf-8') != expected_text:
            raise ValueError('Existing learning draft differs from the exact Host packet; preserved')
        if side.exists():
            saved=json.loads(side.read_text(encoding='utf-8'))
            expected={'request_hash':request_hash,'project':m['project'],'source_references':paths,'proposal_kind':'writeback'}
            if not draft.is_file() or any(saved.get(k) != v for k,v in expected.items()):
                raise ValueError('Existing learning draft binding differs; preserved')
            if saved.get('review_required') is not True:
                from draft_review import DraftReviewer,POLICY
                review=saved.get('auto_review',{});relative=review.get('promoted_to','')
                target=brain.docs/relative
                if (saved.get('review_required') is not False or saved.get('status')!='archived'
                    or review.get('state')!='approved' or review.get('policy')!=POLICY
                    or not relative.startswith('wiki/synthesis/') or not target.resolve().is_relative_to(brain.docs.resolve())
                    or not DraftReviewer(brain,require_committed=True).committed(draft)
                    or not DraftReviewer(brain,require_committed=True).committed(target)):
                    raise ValueError('Existing terminal review lacks committed promotion binding; preserved')
                promoted=json.loads(target.with_suffix('.md.meta.json').read_bytes())
                if (target.read_text(encoding='utf-8')!=expected_text or promoted.get('draft_path')!=draft.relative_to(brain.docs).as_posix()
                    or promoted.get('draft_hash')!=sha(draft.read_bytes()) or promoted.get('review_passes')!=2
                    or promoted.get('review_policy')!=POLICY or promoted.get('source_references')!=paths):
                    raise ValueError('Existing promotion differs from this Host packet; preserved')
                return relative
        return None
    # Validate partial retries before import_file can commit any existing source or log.
    promoted_to=check_existing_draft()
    for source,relative,content in zip(sources,paths,contents):
        path=brain.docs/relative;side=path.with_suffix('.md.meta.json')
        if path.exists() and path.read_bytes() != content:
            raise ValueError('Existing observation bytes differ; preserved')
        if side.exists():
            saved=json.loads(side.read_text(encoding='utf-8'))
            expected={'source_hash':source['content_sha256'],'project':m['project']}
            provenance={'host_observation_event_id':source['source_event_id'],'original_source_id':source['original_source_id'],
                'repository_path':source['repository_path'],'repository_hash':source['repository_hash'],'observed_head':source['head'],
                'original_document_claims_verified':False,'verification_scope':'Historical literal containment only; not current repository authority or PDF semantic truth'}
            if not path.is_file() or any(saved.get(k) != v for k,v in expected.items()) or any(k in saved and saved[k] != v for k,v in provenance.items()):
                raise ValueError('Existing observation binding differs; preserved')
    if promoted_to:
        from draft_review import DraftReviewer
        if not all(DraftReviewer(brain,require_committed=True).committed(brain.docs/path) for path in paths):
            raise ValueError('Existing reviewed observation sources are not committed; preserved')
        return {'status':'reviewed-learning-draft-already-reviewed','path':draft.relative_to(brain.docs).as_posix(),'duplicate':True,
            'host_manifest_sha256':expected_manifest_sha256,'source_paths':paths,'source_count':len(paths),
            'original_document_count':len({s['original_source_id'] for s in sources}),
            'original_document_claims_verified':False,'knowledge_adopted':False,'reviewer_required':False,'previous_promoted_to':promoted_to}
    imported_paths=[]
    # Use the existing raw text import and original Reviewer criteria, rather than weakening PDF eligibility.
    for source,name,content in zip(sources,names,contents):
        imported=brain.import_file(bundle/name,m['project']);relative=imported['path']
        path=brain.docs/relative;side=path.with_suffix('.md.meta.json')
        saved=json.loads(side.read_text(encoding='utf-8'))
        if sha(path.read_bytes()) != source['content_sha256'] or saved.get('source_hash') != source['content_sha256']:
            raise ValueError('Retained observation source drift')
        fields={'host_observation_event_id':source['source_event_id'],'original_source_id':source['original_source_id'],
            'repository_path':source['repository_path'],'repository_hash':source['repository_hash'],'observed_head':source['head'],
            'original_document_claims_verified':False,'verification_scope':'Historical literal containment only; not current repository authority or PDF semantic truth'}
        for key,value in fields.items():
            if key in saved and saved[key] != value:
                raise ValueError('Existing observation provenance differs')
            saved[key]=value
        atomic_json(side,saved)
        committed=brain.git_commit([side],f'brain: bind Host observation {source["content_sha256"][:12]}')
        # A repeated identical sidecar may have no new commit; the underlying raw import must be tracked.
        imported_paths.append(relative)
    if imported_paths != paths: raise ValueError('Raw import path contract differs')
    created=brain.propose(m['request_id'],m['title'],body,m['project'],references=paths,kind='writeback')
    log=brain.pipeline.log('external_reference_learning_draft_created',path=created['path'],host_manifest_sha256=expected_manifest_sha256,
        source_references=paths,original_document_count=len({s['original_source_id'] for s in sources}),
        original_document_claims_verified=False,knowledge_adopted=False)
    from draft_review import DraftReviewer
    from draft_review_queue import after_commit
    draft=brain.docs/created['path']
    required=[brain.docs/path for path in paths]+[draft]
    prior_committed=all(DraftReviewer(brain,require_committed=True).committed(path) for path in required)
    check_existing_draft()
    tracked=[item for path in required for item in (path,path.with_suffix('.md.meta.json'))]+[log]
    committed=brain.git_commit(tracked,f'brain: record Host learning draft {m["request_id"]}')
    if not all(DraftReviewer(brain,require_committed=True).committed(path) for path in required):
        raise ValueError('Learning draft/source durable commit incomplete; retry the same exact packet')
    if not prior_committed:
        after_commit(brain,committed,'writeback')
    return {'status':'reviewed-learning-draft-created','path':created['path'],'duplicate':created['duplicate'],
        'host_manifest_sha256':expected_manifest_sha256,'source_paths':paths,'source_count':len(paths),
        'original_document_count':len({s['original_source_id'] for s in sources}),
        'original_document_claims_verified':False,'knowledge_adopted':False,'reviewer_required':True}



