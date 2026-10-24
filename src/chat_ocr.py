"""Optional, local-only Chat OCR mailbox. No model, command or knowledge calls."""
from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import ipaddress
import json
import os
from pathlib import Path
import re
import sqlite3
import ssl
import stat
import threading
import time

MIB = 1024 * 1024
MAX_INPUT, MAX_REPORT, MAX_ARTIFACT = 4*MIB, 16*MIB, MIB
JOB = re.compile(r'[a-f0-9]{32}')
HASH = re.compile(r'[a-f0-9]{64}')
CID = re.compile(r'[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}')
CODE = re.compile(r'[A-Z][A-Z0-9_]{0,79}')
FORMATS = {'md':'text/markdown', 'json':'application/json', 'html':'text/html', 'csv':'text/csv'}
TERMINAL = {'complete','failed','cancelled','expired','deleted'}
PROGRESS = {'queued','running','paused','cancelling','expiring'}


class OcrError(Exception):
    def __init__(self, code, status=400):
        self.code, self.status = code, status
        super().__init__(code)


def require(ok, code='ocr_invalid_input', status=400):
    if not ok: raise OcrError(code, status)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def strict_json(raw):
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out: raise ValueError('duplicate key')
            out[key] = value
        return out
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite')))


def fields(value, required, optional=()):
    require(type(value) is dict and set(required) <= set(value) <= set(required)|set(optional))


def identifier(value, pattern=JOB):
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def decoded(value, limit):
    require(isinstance(value, str) and len(value) <= 4*((limit+2)//3), 'ocr_size_limit', 413)
    try: raw = base64.b64decode(value, validate=True)
    except (ValueError, TypeError): raise OcrError('ocr_invalid_base64') from None
    require(0 < len(raw) <= limit, 'ocr_size_limit', 413)
    return raw


def magic(raw, extension):
    return ((extension == '.png' and raw.startswith(b'\x89PNG\r\n\x1a\n')) or
            (extension == '.jpg' and raw.startswith(b'\xff\xd8\xff')) or
            (extension == '.pdf' and raw.startswith(b'%PDF-')) or
            (extension == '.webp' and len(raw) >= 12 and raw[:4] == b'RIFF' and raw[8:12] == b'WEBP'))


def guarded(path):
    path = Path(path).absolute()
    for parent in (path, *path.parents):
        try:
            info = parent.lstat()
        except FileNotFoundError: continue
        require(not parent.is_symlink() and not (getattr(info, 'st_file_attributes', 0) &
                getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 1024)), 'ocr_unsafe_storage', 503)
    return path


def enabled(root):
    path = Path(root)/'config/chat-ocr.json'
    if not path.exists(): return False
    try:
        require(path.stat().st_size <= 1024)
        value = strict_json(path.read_bytes())
        fields(value, ('enabled',)); require(type(value['enabled']) is bool)
        return value['enabled']
    except (OSError, ValueError, OcrError): return False


class Mailbox:
    """One configured Host owns assignment. Metadata tombstones are never reused."""
    def __init__(self, root, client_ip, owner, host_pin, chat_pin, clock=time.time):
        self.root, self.client_ip, self.owner = Path(root), client_ip, owner
        self.host_pin, self.chat_pin, self.clock = host_pin, chat_pin, clock
        require(identifier(host_pin, HASH) and identifier(chat_pin, HASH), 'ocr_auth_unconfigured', 503)
        require(not ipaddress.ip_address(client_ip).is_loopback, 'ocr_auth_unconfigured', 503)
        self.directory = guarded(self.root/'runtime/chat-ocr')
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path, self.lock = self.directory/'mailbox.sqlite3', threading.RLock()
        self.max_active, self.max_records, self.quota = 8, 10000, 256*MIB
        self.input_ttl, self.result_ttl = 2*3600, 24*3600
        with self.db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS jobs(
                id TEXT PRIMARY KEY, conversation TEXT NOT NULL, binding TEXT NOT NULL,
                input_hash TEXT NOT NULL, extension TEXT NOT NULL, formats TEXT NOT NULL,
                created REAL NOT NULL, expires REAL NOT NULL, result_expires REAL,
                state TEXT NOT NULL, assigned INTEGER NOT NULL DEFAULT 0,
                registered INTEGER NOT NULL DEFAULT 0, cancel INTEGER NOT NULL DEFAULT 0,
                worker_done INTEGER NOT NULL DEFAULT 0, pages INTEGER NOT NULL DEFAULT 0,
                completed INTEGER NOT NULL DEFAULT 0, wait_reason TEXT,
                report_hash TEXT, error TEXT)''')
        # Crash leftovers are known generated files. Keep them quota-accounted; do not adopt.

    @classmethod
    def from_root(cls, root, client_ip, owner):
        if not enabled(root): return None
        try:
            policy = strict_json((Path(root)/'config/slack-server.json').read_bytes())
            pem = (Path(root)/'config/tls/client.pem').read_text(encoding='ascii')
            pin = hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest()
            return cls(root, client_ip, owner, policy['host_certificate_sha256'], pin)
        except (OSError, ValueError, KeyError, OcrError): return None

    @contextlib.contextmanager
    def db(self):
        guarded(self.path)
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA secure_delete=ON')
        db.execute('BEGIN IMMEDIATE')
        # No content is put in SQLite; DELETE journal avoids retained WAL copies.
        try:
            with db: yield db
        finally: db.close()

    def authenticate(self, role, ip, cert, owner=None):
        require(bool(cert), 'ocr_client_denied', 403)
        fingerprint = hashlib.sha256(cert).hexdigest()
        if role == 'worker':
            require(ip == self.client_ip and owner == self.owner and
                    hmac.compare_digest(fingerprint, self.host_pin), 'ocr_worker_denied', 403)
        else:
            require(role == 'chat' and ip in ('127.0.0.1','::1') and
                    hmac.compare_digest(fingerprint, self.chat_pin), 'ocr_chat_denied', 403)

    def file(self, jid, suffix):
        require(identifier(jid)); return guarded(self.directory/(jid+suffix))

    def write(self, jid, suffix, raw):
        path = self.file(jid, suffix); temp = self.file(jid, suffix+'.incoming')
        if suffix == '.report':
            # Recover a crash between atomic payload publication and metadata commit,
            # only after the authenticated report has passed full job validation.
            if path.exists():
                require(path.read_bytes() == raw, 'ocr_storage_conflict',409)
                return
            if temp.exists():
                require(temp.read_bytes() == raw, 'ocr_storage_conflict',409)
                temp.replace(path); return
        usage = sum(p.stat().st_size for p in self.directory.iterdir() if p.is_file())
        require(usage+len(raw) <= self.quota, 'ocr_storage_full', 429)
        require(not temp.exists() and not path.exists(), 'ocr_storage_conflict', 409)
        with temp.open('xb') as out:
            out.write(raw); out.flush(); os.fsync(out.fileno())
        temp.replace(path)

    def remove(self, jid, suffix):
        self.file(jid, suffix).unlink(missing_ok=True)

    def conversation_exists(self, cid):
        require(identifier(cid, CID))
        path = guarded(self.root/'runtime/chat.sqlite3')
        require(path.exists(), 'ocr_conversation_missing', 404)
        db = sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=10)
        try: require(db.execute('SELECT 1 FROM conversations WHERE id=?', (cid,)).fetchone() is not None,
                     'ocr_conversation_missing', 404)
        finally: db.close()

    def row(self, db, jid, cid=None):
        require(identifier(jid)); row = db.execute('SELECT * FROM jobs WHERE id=?', (jid,)).fetchone()
        require(row is not None and (cid is None or row['conversation'] == cid), 'ocr_not_found', 404)
        return row

    def sweep(self, db):
        for row in db.execute('SELECT * FROM jobs').fetchall():
            jid = row['id']
            if row['state'] not in TERMINAL and self.clock() >= row['expires']:
                db.execute("UPDATE jobs SET state='expired',cancel=1,worker_done=CASE WHEN assigned=0 THEN 1 ELSE worker_done END WHERE id=?", (jid,))
                for suffix in ('.input','.input.incoming','.report','.report.incoming'): self.remove(jid,suffix)
            elif row['result_expires'] is not None and self.clock() >= row['result_expires']:
                self.remove(jid, '.report'); self.remove(jid, '.report.incoming')
                db.execute("UPDATE jobs SET state='expired',cancel=1,result_expires=NULL WHERE id=?", (jid,))
        known = {r[0] for r in db.execute('SELECT id FROM jobs')}
        for path in self.directory.iterdir():
            match = re.fullmatch(r'([a-f0-9]{32})\.(input|report)(\.incoming)?', path.name)
            if match and match[1] not in known:
                guarded(path)
                ttl = self.input_ttl if match[2] == 'input' else self.result_ttl
                if self.clock()-path.stat().st_mtime >= ttl: path.unlink()

    def maintenance(self):
        while True:
            time.sleep(60)
            try:
                with self.lock, self.db() as db: self.sweep(db)
            except Exception:
                # Fixed metadata only; a failed sweep does not discard evidence.
                import logging
                logging.error('chat_ocr_maintenance_unavailable')

    def submit(self, value):
        fields(value, ('job_id','conversation_id','input_sha256','extension','formats','bytes'))
        jid, cid, sha, ext, formats = (value[k] for k in ('job_id','conversation_id','input_sha256','extension','formats'))
        require(identifier(jid) and identifier(cid, CID) and identifier(sha, HASH))
        require(ext in ('.png','.jpg','.webp','.pdf'))
        require(type(formats) is list and 1 <= len(formats) <= 4 and all(type(f) is str for f in formats))
        require(len(set(formats)) == len(formats) and 'md' in formats and set(formats) <= set(FORMATS))
        raw = decoded(value['bytes'], MAX_INPUT)
        require(hashlib.sha256(raw).hexdigest() == sha and magic(raw, ext), 'ocr_input_binding')
        binding = hashlib.sha256(canonical({k:value[k] for k in value if k != 'bytes'})).hexdigest()
        self.conversation_exists(cid)
        with self.lock, self.db() as db:
            self.sweep(db)
            old = db.execute('SELECT * FROM jobs WHERE id=?', (jid,)).fetchone()
            if old:
                require(old['binding'] == binding, 'ocr_id_conflict', 409)
                return self.public(old)
            require(db.execute('SELECT count(*) FROM jobs').fetchone()[0] < self.max_records, 'ocr_tombstone_limit', 429)
            require(db.execute("SELECT count(*) FROM jobs WHERE state NOT IN ('complete','failed','cancelled','expired','deleted') OR (assigned=1 AND worker_done=0)").fetchone()[0] < self.max_active,
                    'ocr_queue_full', 429)
            require(db.execute("SELECT 1 FROM jobs WHERE conversation=? AND state NOT IN ('complete','failed','cancelled','expired','deleted')", (cid,)).fetchone() is None,
                    'ocr_conversation_busy', 409)
            self.write(jid, '.input', raw)
            db.execute('''INSERT INTO jobs(id,conversation,binding,input_hash,extension,formats,created,expires,state)
                          VALUES(?,?,?,?,?,?,?,?,'queued')''', (jid,cid,binding,sha,ext,json.dumps(formats),self.clock(),self.clock()+self.input_ttl))
            return self.public(self.row(db, jid))

    def next(self, value):
        fields(value, ('schema',)); require(value['schema'] == 'localbrain.chat-ocr.poll.v1')
        with self.lock, self.db() as db:
            self.sweep(db)
            row = db.execute('SELECT * FROM jobs WHERE assigned=1 AND worker_done=0 ORDER BY created,id LIMIT 1').fetchone()
            if row is None:
                row = db.execute("SELECT * FROM jobs WHERE assigned=0 AND cancel=0 AND state='queued' ORDER BY created,id LIMIT 1").fetchone()
            if row is None: return {'job':None}
            db.execute('UPDATE jobs SET assigned=1 WHERE id=?', (row['id'],))
            raw = None
            if not row['registered'] and not row['cancel']:
                raw = self.file(row['id'], '.input').read_bytes()
                require(hashlib.sha256(raw).hexdigest() == row['input_hash'], 'ocr_input_corrupt', 503)
            return {'job':dict(schema='localbrain.chat-ocr.job.v1',job_id=row['id'],conversation_id=row['conversation'],
                 input_sha256=row['input_hash'],extension=row['extension'],formats=json.loads(row['formats']),
                 bytes=base64.b64encode(raw).decode() if raw is not None else None,cancel_requested=bool(row['cancel']))}

    def validate_result(self, value, row):
        fields(value, ('schema','job_id','input_sha256','state','result','artifacts','warnings','external_ai','automatic_knowledge_adoption'))
        require(value['external_ai'] == 'deny' and value['automatic_knowledge_adoption'] is False)
        result = value['result']; require(type(result) is dict)
        fields(result, ('job_id','input_file_ref','input_sha256','profile','origin','status','text','pages',
                        'needs_review','external_ai','automatic_knowledge_adoption'), ('experimental',))
        require(result['profile'] in ('AUTO','PRINTED_TEXT','PDF_DOCUMENT','TABLE_LAYOUT','HANDWRITING') and
                type(result['needs_review']) is bool and ('experimental' not in result or type(result['experimental']) is bool))
        oid = hashlib.sha256(('chat_'+row['id']).encode()).hexdigest()[:32]
        require(result.get('job_id') == oid and result.get('input_file_ref') == 'ocr:'+oid+'/input' and
                result.get('input_sha256') == row['input_hash'] and result.get('origin') == 'chat' and
                result.get('external_ai') == 'deny' and result.get('automatic_knowledge_adoption') is False and
                result.get('status') == 'complete' and isinstance(result.get('text'), str), 'ocr_result_binding')
        pages = result.get('pages'); require(type(pages) is list and 1 <= len(pages) <= 25, 'ocr_page_limit')
        require(len(pages) >= row['pages'] and len(pages) >= row['completed'], 'ocr_progress_regression',409)
        for index, page in enumerate(pages):
            require(type(page) is dict and page.get('page') == index and page.get('input_sha256') == row['input_hash'] and
                    page.get('status') == 'complete' and page.get('external_ai') == 'deny' and page.get('network_attempts') == 0 and
                    type(page.get('tables')) is list and isinstance(page.get('text'), str), 'ocr_page_binding')
            require(page.get('source_method') == 'ocr' and type(page.get('needs_review')) is bool and
                    type(page.get('spans')) is list and len(page['spans']) <= 2500 and len(page['tables']) <= 32 and
                    type(page.get('warnings')) is list and all(isinstance(w,str) for w in page['warnings']))
            engine = page.get('engine'); coordinate = page.get('coordinate_system')
            require(type(engine) is dict and engine.get('engine') == 'RapidOCR' and engine.get('runtime') == 'onnxruntime' and
                    engine.get('requested_providers') == ['CPUExecutionProvider'] and type(engine.get('threads')) is int and engine['threads'] == 2)
            require(type(coordinate) is dict and coordinate.get('origin') == 'top_left' and
                    coordinate.get('units') in ('original_image_pixels','pdf_display_points'))
            for key in ('width','height','raster_width','raster_height'):
                require(type(coordinate.get(key)) in (int,float) and coordinate[key] > 0)
        artifacts = value['artifacts']; warnings = value['warnings']
        require(type(warnings) is list and len(warnings) <= 8 and all(identifier(w, CODE) for w in warnings))
        require(type(artifacts) is list and 1 <= len(artifacts) <= 4)
        allowed = set(json.loads(row['formats'])) | ({'json'} if any(p['tables'] for p in pages) else set())
        seen = set()
        for artifact in artifacts:
            fields(artifact, ('format','filename','mime_type','sha256','bytes'))
            fmt = artifact['format']; require(isinstance(fmt,str) and fmt in allowed and fmt not in seen)
            seen.add(fmt)
            require(artifact['filename'] == 'ocr-'+row['id']+'.'+fmt and artifact['mime_type'] == FORMATS[fmt])
            raw = decoded(artifact['bytes'], MAX_ARTIFACT)
            require(identifier(artifact['sha256'],HASH) and hashlib.sha256(raw).hexdigest() == artifact['sha256'], 'ocr_artifact_hash')
            try: raw.decode('utf-8')
            except UnicodeDecodeError: raise OcrError('ocr_artifact_encoding') from None
        require('md' in seen, 'ocr_markdown_required')
        require(not any(p['tables'] for p in pages) or 'json' in seen, 'ocr_table_json_required')

    def update(self, value):
        require(type(value) is dict and value.get('schema') == 'localbrain.chat-ocr.update.v1')
        require(identifier(value.get('job_id')) and identifier(value.get('input_sha256'), HASH))
        state = value.get('state'); require(isinstance(state, str) and state in (PROGRESS | (TERMINAL-{'deleted'})))
        report = canonical(value); require(len(report) <= MAX_REPORT, 'ocr_size_limit', 413)
        digest = hashlib.sha256(report).hexdigest()
        with self.lock, self.db() as db:
            self.sweep(db); row = self.row(db,value['job_id'])
            require(row['assigned'] and row['input_hash'] == value['input_sha256'], 'ocr_assignment_mismatch', 409)
            if row['report_hash'] == digest: return {'accepted':True}
            require(not row['worker_done'], 'ocr_terminal_conflict', 409)
            terminal = state in TERMINAL
            if state == 'complete':
                try: self.validate_result(value, row)
                except OcrError:
                    # A bound, terminal worker report that cannot be published must
                    # become a visible failure, not an endless frozen-report retry.
                    state = 'failed'
                    value = dict(schema=value['schema'],job_id=row['id'],input_sha256=row['input_hash'],
                                 state=state,error_code='OCR_INVALID_RESULT')
            elif terminal:
                fields(value, ('schema','job_id','input_sha256','state'), ('error_code',))
                require('error_code' not in value or identifier(value['error_code'],CODE))
            else:
                fields(value, ('schema','job_id','input_sha256','state'), ('page_count','completed_pages','wait_reason'))
                require(row['state'] != 'cancelling' or state in ('cancelling','expiring'), 'ocr_state_regression', 409)
                has_counts = 'page_count' in value or 'completed_pages' in value
                require(not has_counts or ('page_count' in value and 'completed_pages' in value))
                require(has_counts or state == 'paused')
                if has_counts:
                    pages, count = value['page_count'], value['completed_pages']
                    require(type(pages) is int and type(count) is int and 0 <= count <= pages <= 25, 'ocr_page_limit')
                    require(count >= row['completed'] and pages >= row['pages'], 'ocr_progress_regression', 409)
                reason = value.get('wait_reason')
                require(reason is None or identifier(reason,CODE))
            if row['cancel']:
                # Suppress stale completion; keep assignment until a terminal worker ACK.
                if terminal:
                    db.execute('UPDATE jobs SET worker_done=1,report_hash=? WHERE id=?', (digest,row['id']))
                return {'accepted':True}
            self.conversation_exists(row['conversation'])
            registered = terminal or ('page_count' in value and 'completed_pages' in value)
            if state == 'complete':
                self.write(row['id'], '.report', report)
                db.execute('UPDATE jobs SET result_expires=? WHERE id=?', (self.clock()+self.result_ttl,row['id']))
            db.execute('''UPDATE jobs SET state=?,registered=MAX(registered,?),worker_done=?,
                pages=?,completed=?,wait_reason=?,report_hash=?,error=? WHERE id=?''',
                (state,int(registered),int(terminal),len(value['result']['pages']) if state=='complete' else value.get('page_count',row['pages']),
                 len(value['result']['pages']) if state=='complete' else value.get('completed_pages',row['completed']),
                 value.get('wait_reason'),digest,value.get('error_code'),row['id']))
        # Registration metadata must commit before source deletion (restart/lost ACK).
        if registered: self.remove(row['id'], '.input')
        return {'accepted':True}

    def public(self, row):
        result = dict(job_id=row['id'],conversation_id=row['conversation'],state=row['state'],
                      page_count=row['pages'],completed_pages=row['completed'],wait_reason=row['wait_reason'],
                      cancel_requested=bool(row['cancel']),worker_quiescent=bool(row['worker_done']),
                      expires_at=row['result_expires'] or row['expires'],error_code=row['error'],artifacts=[])
        if row['state'] == 'complete':
            value = strict_json(self.file(row['id'], '.report').read_bytes())
            require(hashlib.sha256(canonical(value)).hexdigest() == row['report_hash'], 'ocr_report_corrupt',503)
            result['artifacts'] = [{k:a[k] for k in ('format','filename','mime_type','sha256')} for a in value['artifacts']]
            text = value['result']['text']; result['text'] = text[:32768]
            result['preview_truncated'] = len(text) > 32768
            result['warnings'] = value['warnings']
        return result

    def chat(self, action, value):
        if action == 'submit': return self.submit(value)
        if action == 'status':
            fields(value, ('conversation_id',)); cid = value['conversation_id']; self.conversation_exists(cid)
            with self.lock, self.db() as db:
                self.sweep(db)
                rows=db.execute("SELECT * FROM jobs WHERE conversation=? AND state!='deleted' ORDER BY created DESC,id DESC LIMIT 64", (cid,)).fetchall()
                return {'jobs':[self.public(r) for r in reversed(rows)]}
        if action == 'forget':
            fields(value, ('conversation_id',)); cid = value['conversation_id']; self.conversation_exists(cid)
            with self.lock, self.db() as db:
                self.sweep(db)
                rows = db.execute('SELECT * FROM jobs WHERE conversation=?',(cid,)).fetchall()
                require(all(r['state'] in TERMINAL and (not r['assigned'] or r['worker_done']) for r in rows), 'ocr_conversation_busy',409)
                for row in rows:
                    self.remove(row['id'], '.input'); self.remove(row['id'], '.report')
                db.execute("UPDATE jobs SET state='deleted',cancel=1,result_expires=NULL WHERE conversation=?", (cid,))
                return {'deleted':True}
        fields(value, ('conversation_id','job_id'), ('format',) if action == 'artifact' else ())
        self.conversation_exists(value['conversation_id'])
        with self.lock, self.db() as db:
            self.sweep(db); row = self.row(db,value['job_id'],value['conversation_id'])
            if action == 'cancel':
                if row['state'] not in TERMINAL:
                    db.execute("UPDATE jobs SET cancel=1,state='cancelled',worker_done=CASE WHEN assigned=0 THEN 1 ELSE worker_done END WHERE id=?", (row['id'],))
                    self.remove(row['id'], '.input'); self.remove(row['id'], '.report')
                return self.public(self.row(db,row['id']))
            require(action in ('artifact','result'), 'ocr_not_found', 404)
            require(row['state'] == 'complete' and not row['cancel'], 'ocr_result_unavailable', 410)
            report = strict_json(self.file(row['id'], '.report').read_bytes())
            require(hashlib.sha256(canonical(report)).hexdigest() == row['report_hash'], 'ocr_report_corrupt',503)
            if action == 'result': return {'text':report['result']['text']}
            require(isinstance(value.get('format'),str) and value['format'] in FORMATS)
            found = next((a for a in report['artifacts'] if a['format'] == value['format']),None)
            require(found is not None,'ocr_not_found',404)
            return found

    def dispatch(self, action, value):
        if action == 'next': return self.next(value)
        if action == 'update': return self.update(value)
        return self.chat(action, value)
