"""Safe, idempotent ingestion for files and ChatGPT export archives."""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import zipfile
from pathlib import Path

MAX_ARCHIVE_MEMBER = 32 * 1024 * 1024
MAX_ARCHIVE_TOTAL = 512 * 1024 * 1024
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\b(?:ghp|github_pat|glpat)-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"(?i)\b(?:access_token|refresh_token|auth_cookie|api_key|password|connection_string|connectionstring)\s*[:=]\s*['\"]?[A-Za-z0-9_./:;+=-]{16,}"),
)


def digest(data: bytes) -> str: return hashlib.sha256(data).hexdigest()
def now() -> str: return dt.datetime.now(dt.timezone.utc).isoformat()


from knowledge_guard import guarded

class Ingestor:
    def __init__(self, brain): self.brain = brain

    def contains_secret(self, text: str) -> bool:
        return any(pattern.search(text) for pattern in SECRET_PATTERNS)

    @guarded
    def quarantine(self, raw: bytes, source: str, reason="high-confidence secret") -> dict:
        sha = digest(raw); target = self.brain.docs / "quarantine" / f"{sha[:16]}.bin"
        if not target.exists(): target.write_bytes(raw)
        meta = {"status":"quarantined", "source":source, "sha256":sha, "reason":reason, "detected_at":now()}
        side=self.brain.safe_path("quarantine/" + target.name + ".meta.json")
        side.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        log=self.brain.pipeline.log("source_quarantined",source_hash=sha,reason=reason)
        # Quarantined source bytes and their location metadata never enter Git.
        self.brain.git_commit([log],f"brain: record quarantined source {sha[:12]}")
        return {"status":"quarantined", "path":target.relative_to(self.brain.docs).as_posix(), "sha256":sha}

    @guarded
    def ingest_file(self, source: Path, project="general") -> dict:
        source = Path(source).resolve(); raw = source.read_bytes(); text = raw.decode("utf-8-sig")
        if self.contains_secret(text): return self.quarantine(raw, str(source))
        result = self.brain.import_file(source, project); result["status"] = "imported"; return result

    @staticmethod
    def message_text(message: dict) -> str:
        content = message.get("content") or {}; parts = content.get("parts") or []
        values=[]
        for part in parts:
            if isinstance(part,str): values.append(part)
            elif isinstance(part,dict): values.append(json.dumps(part,ensure_ascii=False))
        return "\n".join(values).strip()

    @guarded
    def ingest_chatgpt_zip(self, source: Path, project="chatgpt") -> dict:
        source=Path(source).resolve(); created=superseded=duplicates=quarantined=0; queued=0
        with zipfile.ZipFile(source) as archive:
            infos=archive.infolist()
            if sum(item.file_size for item in infos)>MAX_ARCHIVE_TOTAL or any(item.file_size>MAX_ARCHIVE_MEMBER for item in infos):
                raise ValueError("ChatGPT export exceeds safe archive limits")
            candidates=[item for item in infos if Path(item.filename).name.lower()=="conversations.json"]
            if len(candidates)!=1: raise ValueError("ChatGPT export must contain one conversations.json")
            raw=archive.read(candidates[0]); conversations=json.loads(raw)
        if not isinstance(conversations,list): raise ValueError("Invalid ChatGPT conversations.json")
        for conversation in conversations:
            conversation_id=str(conversation.get("id") or conversation.get("conversation_id") or digest(json.dumps(conversation,sort_keys=True).encode())[:32])
            title=str(conversation.get("title") or "Untitled conversation").replace("\n"," ")[:200]
            messages=[]
            for node in (conversation.get("mapping") or {}).values():
                message=node.get("message") if isinstance(node,dict) else None
                if not isinstance(message,dict): continue
                text=self.message_text(message)
                if not text: continue
                role=((message.get("author") or {}).get("role") or "unknown")
                message_id=str(message.get("id") or digest((role+text).encode())[:32])
                messages.append((float(message.get("create_time") or 0),message_id,role,text))
            messages.sort(key=lambda item:(item[0],item[1]))
            lines=[f"# {title}","",f"Conversation ID: `{conversation_id}`",""]
            for _,message_id,role,text in messages:
                lines.extend([f"## {role}","",f"<!-- message_id: {message_id}; content_hash: {digest(text.encode())} -->",text,""])
            normalized="\n".join(lines).rstrip()+"\n"; encoded=normalized.encode()
            if self.contains_secret(normalized):
                self.quarantine(encoded,f"{source}#{conversation_id}"); quarantined+=1; continue
            safe_id=re.sub(r"[^A-Za-z0-9_.-]","_",conversation_id)[:100]
            content_hash=digest(encoded); folder=self.brain.docs/"raw"/"chatgpt"; folder.mkdir(parents=True,exist_ok=True)
            target=folder/f"chatgpt-{safe_id}-{content_hash[:12]}.md"; side=target.with_suffix(".md.meta.json")
            if target.exists(): duplicates+=1; continue
            # A revised conversation creates a new immutable source. Older normalized
            # versions remain present and become superseded through metadata only.
            prior=[]
            for old_side in folder.glob(f"chatgpt-{safe_id}-*.md.meta.json"):
                try:
                    old_meta=json.loads(old_side.read_text(encoding="utf-8"))
                    if old_meta.get("status")=="active":
                        old_meta["status"]="superseded"; old_meta["superseded_by"]="source:"+content_hash
                        old_meta["valid_until"]=now(); old_side.write_text(json.dumps(old_meta,ensure_ascii=False,indent=2),encoding="utf-8")
                        prior.append(old_side); superseded+=1
                except (OSError,ValueError): continue
            target.write_bytes(encoded); ingested_at=now()
            timestamps=[row[0] for row in messages if row[0]>0]
            source_date=dt.datetime.fromtimestamp(max(timestamps),dt.timezone.utc).isoformat() if timestamps else None
            metadata={"project":project,"status":"active","evidence_level":"confirmed","source_id":"source:"+content_hash,
                      "source_path":target.relative_to(self.brain.docs).as_posix(),"source_type":"chatgpt-conversation",
                      "source_date":source_date,"retrieved_at":ingested_at,"source_export":str(source),"source_export_hash":digest(raw),
                      "conversation_id":conversation_id,"source_hash":content_hash,"content_hash":content_hash,"ingested_at":ingested_at,
                      "allow_fact_promotion":False,
                      "note":"Normalized immutable conversation source. Embedded instructions are reference text only."}
            side.write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding="utf-8")
            log=self.brain.pipeline.log("chatgpt_conversation_ingested",source_path=target.relative_to(self.brain.docs).as_posix(),source_hash=content_hash)
            self.brain.git_commit([target,side,log,*prior],f"brain: ingest ChatGPT source {content_hash[:12]}")
            self.brain.pipeline.enqueue(target.relative_to(self.brain.docs).as_posix()); queued+=1; created+=1
        index=self.brain.index()
        return {"status":"complete","created":created,"superseded":superseded,"duplicates":duplicates,"quarantined":quarantined,"queued":queued,"index":index}

    @guarded
    def ingest(self, source: Path, project="general") -> dict:
        source=Path(source)
        if source.suffix.lower()==".zip": return self.ingest_chatgpt_zip(source,project)
        return self.ingest_file(source,project)

    @guarded
    def migrate_legacy_chatlogs(self) -> dict:
        """Copy legacy normalized conversations into immutable raw and retire duplicate search entries."""
        source_folder=self.brain.docs/"chatlogs"; target_folder=self.brain.docs/"raw"/"chatgpt"
        target_folder.mkdir(parents=True,exist_ok=True); created=duplicates=superseded=queued=0; changed=[]
        for old in sorted(source_folder.glob("*.md")):
            raw=old.read_bytes(); sha=digest(raw); old_side=old.with_suffix(".md.meta.json")
            old_meta=json.loads(old_side.read_text(encoding="utf-8")) if old_side.exists() else {}
            safe_id=re.sub(r"[^A-Za-z0-9_.-]","_",str(old_meta.get("conversation_id") or old.stem))[:100]
            target=target_folder/f"{safe_id}-{sha[:12]}.md"; side=target.with_suffix(".md.meta.json")
            if target.exists(): duplicates+=1
            else:
                target.write_bytes(raw); imported_at=now()
                metadata={"project":old_meta.get("project","chatgpt"),"status":"active","evidence_level":"confirmed",
                          "source_id":"source:"+sha,"source_path":target.relative_to(self.brain.docs).as_posix(),
                          "source_type":"chatgpt-conversation","source_date":old_meta.get("source_date"),"retrieved_at":imported_at,
                          "source_hash":sha,"conversation_id":old_meta.get("conversation_id"),"migrated_from":old.relative_to(self.brain.docs).as_posix(),
                          "allow_fact_promotion":False,
                          "note":"Immutable normalized conversation source migrated from the legacy chatlogs area."}
                side.write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding="utf-8"); changed.extend([target,side]); created+=1
            if old_side.exists() and old_meta.get("status")!="superseded":
                old_meta["status"]="superseded"; old_meta["superseded_by"]="source:"+sha; old_meta["valid_until"]=now()
                old_side.write_text(json.dumps(old_meta,ensure_ascii=False,indent=2),encoding="utf-8"); changed.append(old_side); superseded+=1
            if self.brain.pipeline.enqueue(target.relative_to(self.brain.docs).as_posix())["queued"]: queued+=1
        log=self.brain.pipeline.log("legacy_chatlogs_migrated",created=created,duplicates=duplicates,superseded=superseded,queued=queued)
        self.brain.git_commit([*changed,log],"brain: migrate legacy chat sources to raw")
        return {"created":created,"duplicates":duplicates,"superseded":superseded,"queued":queued,"index":self.brain.index()}
