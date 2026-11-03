"""Prepare one Gateway-only packet from Git objects and a fresh read-only baseline.

No UAC, service, model, database connection, production write or real GO.
Runtime release source and deployment-procedure commit are separately bound.
"""
import argparse
import hashlib
import io
import json
import re
import subprocess
import zipfile
from pathlib import Path

SOURCE = 'f5df6bd12e9a87333f06948966296f80b1167d35'
DEPLOYMENT = 'gateway-priority-f5df6bd-20261103'
sha = lambda raw: hashlib.sha256(raw).hexdigest()


def require(value, code):
    if not value:
        raise ValueError(code)


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def put(path, value):
    raw = (json.dumps(value, ensure_ascii=False, indent=2)+'\n').encode()
    with path.open('xb') as file:
        file.write(raw)
    return sha(raw)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--repository', type=Path, required=True)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--old-packet', type=Path, required=True)
    parser.add_argument('--procedure-commit', required=True)
    parser.add_argument('--client-source', default='049f0c378f681d73dfde74cc36020f74190c79b0')
    parser.add_argument('--packet', type=Path, required=True)
    a = parser.parse_args()
    require(re.fullmatch('[a-f0-9]{40}', a.procedure_commit) and re.fullmatch('[a-f0-9]{40}', a.client_source), 'commit_required')
    def git(*args):
        return subprocess.run(['git', '-C', str(a.repository), *args], check=True, capture_output=True).stdout
    baseline_raw = a.baseline.read_bytes()
    baseline = json.loads(baseline_raw)
    old_raw = (a.old_packet/'startup-spec.json').read_bytes()
    require(sha(old_raw) == baseline['old_spec_sha256'], 'old_spec_reference_drift')
    old = json.loads(old_raw)
    require(old['source_commit'] == '55a206943b43cf2041742049b7265333dff91ae8', 'old_source_drift')
    require(old['data_root'] == baseline['data_root'], 'data_root_drift')
    names = [record['path'] for record in read(a.old_packet/'release-manifest.json')['files']]
    require(len(names) == 146 and len(set(n.casefold() for n in names)) == 146, 'release_inventory_drift')
    packet = a.packet.absolute()
    packet.mkdir()  # Existing packets are never reused, even after failure.
    payload = packet/'payload'
    payload.mkdir()
    with zipfile.ZipFile(io.BytesIO(git('archive', '--format=zip', SOURCE))) as archive:
        for name in names:
            path = Path(name)
            require(not path.is_absolute() and '..' not in path.parts and ':' not in name and '\\' not in name, 'source_path')
            dest = payload/path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(archive.read(name))
    manifest = {'schema': 'localbrain.code-release-manifest.v1', 'source_commit': SOURCE,
                'files': [{'path': name, 'sha256': sha((payload/name).read_bytes())} for name in sorted(names)]}
    manifest_sha = put(packet/'release-manifest.json', manifest)
    (packet/'baseline.json').write_bytes(baseline_raw)
    (packet/'old-startup-spec.json').write_bytes(old_raw)
    program = Path(r'C:\ProgramData\LocalBrainServer')
    activation = program/'chat-ocr-activations'/DEPLOYMENT
    startup = {**old, 'source_commit': SOURCE, 'manifest_sha256': manifest_sha,
               'release_root': str(program/'releases'/SOURCE),
               'entry_path': str(activation/'chat_ocr_entry.py'),
               'activation_go': str(activation/'activation-go.json')}
    spec_sha = put(packet/'startup-spec.json', startup)
    (packet/'chat_ocr_entry.py').write_bytes((payload/'deployment/chat_ocr_entry.py').read_bytes())
    require(sha((packet/'chat_ocr_entry.py').read_bytes()) == old['entry_sha256'], 'entry_not_same')
    procedures = {}
    for name in ('Apply-GatewayForward.ps1', 'gateway_forward_checks.py', 'Capture-GatewayForward.ps1',
                 'prepare_gateway_forward.py', 'Invoke-GatewayForwardUac.ps1'):
        raw = git('show', a.procedure_commit+':deployment/'+name)
        require(raw == (Path(__file__).parent/name).read_bytes(), 'procedure_worktree_drift')
        (packet/name).write_bytes(raw)
        procedures[name] = sha(raw)
    runtimes = {Path(r['path']).name.lower(): r for r in baseline['runtimes']}
    root = Path(old['data_root'])
    identities = {}
    for name in ('runtime/chat.sqlite3', 'runtime/gateway-jobs.sqlite3', 'runtime/slack-restricted/state.sqlite3',
                 'runtime/index.sqlite3', 'runtime/semantic.sqlite3', 'runtime/chat-ocr/mailbox.sqlite3'):
        path = root/name
        for part in (path, *path.parents):
            st = part.lstat()
            require(not part.is_symlink() and not getattr(st, 'st_file_attributes', 0)&0x400, 'database_reparse_refused')
        require(path.is_file() and path.stat().st_ino != 0, 'database_identity_unavailable')
        identities[name] = format(path.stat().st_ino, '032x')
    require(all(identities[k] == v for k, v in old['databases'].items()), 'old_database_identity_drift')
    intent = {**{k: startup[k] for k in ('source_commit', 'manifest_sha256', 'release_root', 'data_root',
              'entry_path', 'entry_sha256', 'activation_go', 'databases')},
        'schema': 'localbrain.gateway-forward-intent.v1', 'packet': str(packet),
        'deployment_id': DEPLOYMENT, 'program_root': str(program), 'activation_root': str(activation),
        'audit_root': str(program/('chat-ocr-attempt-'+DEPLOYMENT)),
        'procedure_commit': a.procedure_commit, 'procedure_files': procedures,
        'client_source': a.client_source, 'automatic_restore': False,
        'spec_sha256': spec_sha, 'script_sha256': procedures['Apply-GatewayForward.ps1'],
        'checks_sha256': procedures['gateway_forward_checks.py'], 'baseline_sha256': sha(baseline_raw),
        'python': runtimes['python.exe']['path'], 'python_sha256': runtimes['python.exe']['sha256'],
        'powershell': runtimes['pwsh.exe']['path'], 'powershell_sha256': runtimes['pwsh.exe']['sha256'],
        'old_gateway_source': old['source_commit'], 'existing_db_identities': identities,
        'fence_sha256': sha((payload/'deployment/release_fence.ps1').read_bytes()),
        **{k: baseline[k] for k in ('tasks', 'protected_files', 'models', 'config_inventory', 'base_processes',
            'old_spec_path', 'old_spec_sha256', 'old_manifest_sha256', 'old_activation_go_sha256',
            'old_receipt_path', 'old_receipt_sha256', 'old_receipt_intent')}}
    intent_sha = put(packet/'intent.json', intent)
    for operation in ('Apply', 'Restore'):
        put(packet/(operation.lower()+'-go.template.json'), {
            'phase': 'GatewayOnlyMaintenanceGO', 'operation': operation, 'deployment_id': DEPLOYMENT,
            'intent_sha256': intent_sha, 'source_commit': SOURCE, 'manifest_sha256': manifest_sha,
            'script_sha256': intent['script_sha256'], 'client_source': a.client_source,
            'client_active_requests': None, 'client_queued_requests': None,
            'all_chat_slack_continue_idle': False, 'no_new_requests_until_finished': False,
            'continue_gui_verified': False, 'continue_gui_evidence_reference': '',
            'human_approval_reference': '', 'created_utc': '',
            'apply_receipt_sha256': None, 'note': 'TEMPLATE ONLY; not approval or a GO'})
    result = {'status': 'PREPARED_NOT_EXECUTED', 'packet': str(packet), 'intent_sha256': intent_sha,
              'source': SOURCE, 'procedure_commit': a.procedure_commit, 'files': len(names),
              'manifest_sha256': manifest_sha, 'spec_sha256': spec_sha,
              'production_writes': 0, 'task_operations': 0}
    put(packet/'prepared.json', result)
    print(json.dumps(result))


if __name__ == '__main__':
    main()
