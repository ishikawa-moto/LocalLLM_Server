"""Gateway-only forward/restore checks. Never reads business rows or DB/WAL bytes.

Uses the release's existing path, mTLS and schema validation helpers. A schema
and file-identity comparison is not a logical-content checksum or DB backup.
"""
import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import re
import sqlite3
import types
from pathlib import Path


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def require(value, code):
    if not value:
        raise ValueError(code)


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def pinned(path, expected):
    raw = Path(path).read_bytes()
    require(sha(raw) == expected, 'fixed_bytes_drift')
    return raw


def maintenance(go, intent, intent_sha, operation, receipt_sha=None, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    require(go.get('phase') == 'GatewayOnlyMaintenanceGO', 'maintenance_phase')
    for key, expected in (
        ('operation', operation), ('intent_sha256', intent_sha),
        ('deployment_id', intent['deployment_id']), ('source_commit', intent['source_commit']),
        ('script_sha256', intent['script_sha256']), ('manifest_sha256', intent['manifest_sha256']),
        ('client_source', intent['client_source'])):
        require(go.get(key) == expected, 'maintenance_binding')
    for key in ('client_active_requests', 'client_queued_requests'):
        require(type(go.get(key)) is int and go[key] == 0, 'maintenance_counts')
    for key in ('all_chat_slack_continue_idle', 'no_new_requests_until_finished', 'continue_gui_verified'):
        require(go.get(key) is True, 'maintenance_idle')
    for key in ('human_approval_reference', 'continue_gui_evidence_reference'):
        require(isinstance(go.get(key), str) and go[key].strip(), 'human_evidence_missing')
    created = dt.datetime.fromisoformat(go['created_utc'].replace('Z', '+00:00'))
    require(created.tzinfo is not None and 0 <= (now-created).total_seconds() <= 900, 'maintenance_expired')
    if operation == 'Restore':
        require(re.fullmatch('[0-9a-f]{64}', receipt_sha or '') is not None and
                go.get('apply_receipt_sha256') == receipt_sha, 'restore_receipt_binding')


def idle(value):
    require(value.get('state') in ('STOPPED', 'READY'), 'gateway_unknown_state')
    for key in ('active_jobs', 'queued_jobs'):
        require(type(value.get(key)) is int and value[key] == 0, 'gateway_busy')
    activity = value.get('restricted_activity', {})
    for key in ('available', 'stable', 'quiescent'):
        require(activity.get(key) is True, 'restricted_unknown')
    for key in ('active_requests', 'active_applications', 'nonterminal_runs'):
        require(type(activity.get(key)) is int and activity[key] == 0, 'restricted_busy')


def old_chat(c, intent):
    value = c.request(intent, 'chat', '/health')
    require(value.get('chat_service') == 'ok', 'chat_unhealthy')
    # Normal Chat health deliberately has no gate labels. Fixed spec/code and
    # actual PID/birth/instance/command are separately verified by the PS caller.
    return {'health_http': 200, 'chat_service': value['chat_service']}


def mailbox_idle(c, intent):
    root = Path(intent['data_root'])
    path = root/'config/slack-ocr.json'
    if path.exists():
        require(read(c.safe(path)) == {'enabled': False}, 'new_slack_ocr_not_in_scope')
    path = c.safe(root/'runtime/chat-ocr/mailbox.sqlite3')
    require(format(path.stat().st_ino, '032x') == intent['existing_db_identities']['runtime/chat-ocr/mailbox.sqlite3'], 'mailbox_identity_drift')
    db = sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=5)
    try:
        db.execute('PRAGMA query_only=ON')
        count = db.execute("SELECT count(*) FROM jobs WHERE state NOT IN ('complete','failed','cancelled','expired','deleted') OR (assigned=1 AND worker_done=0)").fetchone()[0]
    finally:
        db.close()
    require(count == 0, 'chat_ocr_not_quiescent')
    return {'chat_ocr_nonquiescent_jobs': 0, 'new_slack_ocr_enabled': False}


def restricted_metadata_idle(c, intent):
    """Bounded activity counters only; no IDs, results, messages or approvals."""
    root = Path(intent['data_root'])
    for name, query in (
        ('runtime/gateway-jobs.sqlite3', "SELECT count(*) FROM jobs WHERE state IN ('queued','active')"),
        ('runtime/slack-restricted/state.sqlite3', "SELECT count(*) FROM runs WHERE state NOT IN ('complete','interrupted')")):
        path = c.safe(root/name)
        require(format(path.stat().st_ino, '032x') == intent['existing_db_identities'][name], 'activity_database_identity_drift')
        db = sqlite3.connect(path.as_uri()+'?mode=ro', uri=True, timeout=5)
        try:
            db.execute('PRAGMA query_only=ON')
            require(db.execute(query).fetchone()[0] == 0, 'unfinished_gateway_or_restricted_work')
        finally:
            db.close()


def storage(packet, intent, records):
    # Reuse schema validation from exact release bytes; it reads sqlite_master
    # and PRAGMA metadata only. Never call legacy storage()/quick_check().
    code = pinned(packet/'payload/src/startup_gate.py', records['src/startup_gate.py'])
    gate = types.ModuleType('verified_gateway_schema')
    exec(compile(code, '<verified-startup-guard>', 'exec'), gate.__dict__)
    spec = json.loads(pinned(packet/'startup-spec.json', intent['spec_sha256']))
    gate.configure(spec, intent['spec_sha256'])
    root = Path(intent['data_root'])
    identities = {name: gate.identity(root/name) for name in intent['existing_db_identities']}
    require(identities == intent['existing_db_identities'], 'database_identity_changed')
    result = {}
    for name in sorted(identities):
        if name in gate.SCHEMAS:
            gate.validate_schema(root/name)
        # An existing WAL is honored by a normal read-only SQLite connection.
        # No immutable=1, checkpoint, migration, quick_check, row or file hashing.
        db = sqlite3.connect((root/name).as_uri()+'?mode=ro', uri=True, timeout=5)
        try:
            db.execute('PRAGMA query_only=ON')
            catalog = db.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name').fetchall()
        finally:
            db.close()
        require(gate.identity(root/name) == identities[name], 'database_identity_changed')
        result[name] = {'file_id': identities[name], 'catalog_sha256': sha(json.dumps(catalog, separators=(',', ':')).encode())}
    return {'existing_db_identities': identities, 'databases': result,
            'scope': 'file identity and schema metadata only; business contents not read or compared'}


def assets(packet, intent, c, manifest, records):
    require(manifest['schema'] == 'localbrain.code-release-manifest.v1' and
            manifest['source_commit'] == intent['source_commit'], 'manifest_source_drift')
    require(len(records) == len(manifest['files']), 'duplicate_manifest_path')
    folded = set()
    for name, expected in records.items():
        relative = Path(name)
        require(not relative.is_absolute() and '..' not in relative.parts and
                ':' not in name and '\\' not in name and name.casefold() not in folded, 'manifest_path_invalid')
        folded.add(name.casefold())
        pinned(c.safe(packet/'payload'/relative), expected)
    actual = set()
    for path in (packet/'payload').rglob('*'):
        c.safe(path)
        if path.is_file():
            actual.add(path.relative_to(packet/'payload').as_posix())
    require(actual == set(records), 'payload_inventory_drift')
    for name, key in (('startup-spec.json', 'spec_sha256'), ('chat_ocr_entry.py', 'entry_sha256'),
                      ('Apply-GatewayForward.ps1', 'script_sha256'), ('baseline.json', 'baseline_sha256')):
        pinned(c.safe(packet/name), intent[key])
    startup = read(packet/'startup-spec.json')
    for key in ('source_commit', 'manifest_sha256', 'release_root', 'data_root', 'entry_path',
                'entry_sha256', 'activation_go', 'databases'):
        require(startup[key] == intent[key], 'spec_binding_drift')
    old = json.loads(pinned(c.safe(packet/'old-startup-spec.json'), intent['old_spec_sha256']))
    changed = {'source_commit', 'manifest_sha256', 'release_root', 'entry_path', 'activation_go'}
    require(set(old) == set(startup) and all(startup[k] == old[k] for k in old if k not in changed), 'unapproved_spec_delta')
    require(old['source_commit'] == intent['old_gateway_source'] and old['data_root'] == intent['data_root'], 'old_spec_binding')
    expected_helpers = {'Apply-GatewayForward.ps1', 'gateway_forward_checks.py', 'Capture-GatewayForward.ps1',
                        'prepare_gateway_forward.py', 'Invoke-GatewayForwardUac.ps1'}
    require(set(intent['procedure_files']) == expected_helpers, 'procedure_inventory')
    for name, expected in intent['procedure_files'].items():
        pinned(c.safe(packet/name), expected)
    require(records['deployment/chat_ocr_entry.py'] == intent['entry_sha256'], 'entry_source_drift')
    require(records['deployment/release_fence.ps1'] == intent['fence_sha256'], 'fence_source_drift')
    pinned(c.safe(intent['python']), intent['python_sha256'])
    return {'status': 'ARTIFACTS_VERIFIED', 'source': intent['source_commit'], 'file_count': len(records)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=('Assets', 'Maintenance', 'OldProbe', 'HeldProbe', 'Storage', 'ActiveProbe', 'RestorePreflight'), required=True)
    parser.add_argument('--packet', type=Path, required=True)
    parser.add_argument('--intent-sha256', required=True)
    parser.add_argument('--go-sha256')
    parser.add_argument('--operation', choices=('Apply', 'Restore'), default='Apply')
    parser.add_argument('--apply-receipt-sha256')
    parser.add_argument('--restore-case', choices=('OldStopped', 'NewStopped', 'HeldNew', 'ActiveNew'))
    args = parser.parse_args()
    packet = args.packet
    intent = json.loads(pinned(packet/'intent.json', args.intent_sha256))
    require(intent.get('schema') == 'localbrain.gateway-forward-intent.v1' and intent['packet'] == str(packet), 'intent_scope')
    pinned(packet/'gateway_forward_checks.py', intent['checks_sha256'])
    manifest = json.loads(pinned(packet/'release-manifest.json', intent['manifest_sha256']))
    records = {record['path']: record['sha256'] for record in manifest['files']}
    helper = packet/'payload/deployment/chat_ocr_checks.py'
    pinned(helper, records['deployment/chat_ocr_checks.py'])
    spec = importlib.util.spec_from_file_location('existing_public_checks', helper)
    c = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(c)
    c.safe(packet)
    if args.mode == 'Assets':
        result = assets(packet, intent, c, manifest, records)
    elif args.mode == 'Maintenance':
        go = json.loads(pinned(packet/('restore-go.json' if args.operation == 'Restore' else 'apply-go.json'), args.go_sha256))
        maintenance(go, intent, args.intent_sha256, args.operation, args.apply_receipt_sha256)
        result = {'status': 'FRESH_MAINTENANCE_GO', 'operation': args.operation}
    elif args.mode in ('OldProbe', 'ActiveProbe'):
        value = c.request(intent, 'gateway', '/health')
        idle(value)
        expected = intent['old_gateway_source'] if args.mode == 'OldProbe' else intent['source_commit']
        require(value.get('release_commit') == expected, 'gateway_source_drift')
        if args.mode == 'ActiveProbe':
            ready = c.request(intent, 'gateway', '/v1/chat-ocr/readiness')
            require(ready.get('status') == 'CHAT_OCR_SERVER_READY' and ready.get('source') == expected and
                    ready.get('existing_history_preserved') is True and ready.get('real_authenticated_endpoints_verified') is True, 'active_readiness_binding')
        result = {'status': 'FRESH_IDLE_SNAPSHOT', 'state': value['state'], 'active': 0,
                  'queued': 0, 'chat': old_chat(c, intent), 'source': expected,
                  'ocr': mailbox_idle(c, intent)}
    elif args.mode == 'HeldProbe' or (args.mode == 'RestorePreflight' and args.restore_case == 'HeldNew'):
        value = c.request(intent, 'gateway', '/health')
        require(value.get('status') == 'ACTIVATION_HELD' and value.get('role') == 'gateway' and
                value.get('source') == intent['source_commit'] and value.get('manifest_sha256') == intent['manifest_sha256'] and
                value.get('spec_sha256') == intent['spec_sha256'] and type(value.get('pid')) is int and
                value.get('existing_history_bound') is True, 'held_gateway_binding')
        require(c.request(intent, 'gateway', '/v1/chat-ocr/readiness') == {'status': 'CHAT_OCR_SERVER_NOT_READY'}, 'held_readiness')
        result = {'status': 'HELD_ENDPOINTS_VERIFIED', 'processes': {'gateway': value}, 'chat': old_chat(c, intent)}
        if args.mode == 'RestorePreflight':
            restricted_metadata_idle(c, intent)
            result['storage'] = storage(packet, intent, records)
            result['ocr'] = mailbox_idle(c, intent)
    elif args.mode == 'RestorePreflight':
        require(args.restore_case in ('OldStopped', 'NewStopped', 'ActiveNew'), 'restore_case_required')
        restricted_metadata_idle(c, intent)
        result = {'status': 'RESTORE_METADATA_VERIFIED', 'restore_case': args.restore_case,
                  'chat': old_chat(c, intent), 'ocr': mailbox_idle(c, intent),
                  'storage': storage(packet, intent, records)}
        if args.restore_case == 'ActiveNew':
            value = c.request(intent, 'gateway', '/health')
            idle(value)
            require(value.get('release_commit') == intent['source_commit'], 'restore_active_source_drift')
            ready = c.request(intent, 'gateway', '/v1/chat-ocr/readiness')
            require(ready.get('status') == 'CHAT_OCR_SERVER_READY' and ready.get('source') == intent['source_commit'], 'restore_active_readiness')
    else:
        result = storage(packet, intent, records)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
