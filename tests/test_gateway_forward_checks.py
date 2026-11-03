"""Focused safeguards for the new deployment delta; no production access."""
import datetime as dt
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('gateway_forward_checks', ROOT/'deployment/gateway_forward_checks.py')
checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checks)


class GatewayForwardChecks(unittest.TestCase):
    def test_maintenance_requires_fresh_explicit_operation_and_restore_binding(self):
        now = dt.datetime.now(dt.timezone.utc)
        intent = dict(deployment_id='public-fixture', source_commit='a'*40, script_sha256='b'*64,
                      manifest_sha256='c'*64, client_source='d'*40)
        go = dict(phase='GatewayOnlyMaintenanceGO', operation='Apply', intent_sha256='e'*64,
                  **intent, client_active_requests=0, client_queued_requests=0,
                  all_chat_slack_continue_idle=True, no_new_requests_until_finished=True,
                  continue_gui_verified=True, human_approval_reference='public operator confirmation',
                  continue_gui_evidence_reference='public Continue marker', created_utc=now.isoformat())
        checks.maintenance(go, intent, 'e'*64, 'Apply', now=now)
        for change in (dict(operation='Restore'), dict(continue_gui_verified=False),
                       dict(client_active_requests=True), dict(human_approval_reference=''),
                       dict(created_utc=(now-dt.timedelta(seconds=901)).isoformat()),
                       dict(created_utc=(now+dt.timedelta(seconds=1)).isoformat())):
            with self.assertRaises(ValueError):
                checks.maintenance({**go, **change}, intent, 'e'*64, 'Apply', now=now)
        restore = {**go, 'operation': 'Restore', 'apply_receipt_sha256': 'f'*64}
        checks.maintenance(restore, intent, 'e'*64, 'Restore', 'f'*64, now)
        with self.assertRaises(ValueError):
            checks.maintenance(restore, intent, 'e'*64, 'Restore', '0'*64, now)

    def test_storage_uses_only_schema_metadata_and_never_business_rows(self):
        code = (ROOT/'src/startup_gate.py').read_bytes()
        gate = types.ModuleType('schema_fixture')
        exec(compile(code, '<schema-fixture>', 'exec'), gate.__dict__)
        with tempfile.TemporaryDirectory() as directory:
            packet = Path(directory)
            data = packet/'data'
            data.mkdir()
            identities = {}
            for name, ddl in gate.REFERENCE_DDL.items():
                path = data/name
                path.parent.mkdir(parents=True, exist_ok=True)
                with sqlite3.connect(path) as db:
                    db.executescript(ddl)
                    if name.endswith('chat.sqlite3'):
                        db.execute('PRAGMA journal_mode=WAL')
                        db.execute("INSERT INTO conversations VALUES('public','fixture','fixture',0)")
                        db.execute("INSERT INTO messages(conversation,role,content,metadata) VALUES('public','user','PUBLIC_SENTINEL','{}')")
                db.close()
                identities[name] = format(path.stat().st_ino, '032x')
            payload = packet/'payload/src'
            payload.mkdir(parents=True)
            (payload/'startup_gate.py').write_bytes(code)
            startup = dict(data_root=str(data), databases=identities, activation_go=str(packet/'missing-go.json'))
            raw = json.dumps(startup).encode()
            (packet/'startup-spec.json').write_bytes(raw)
            intent = dict(data_root=str(data), existing_db_identities=identities, spec_sha256=checks.sha(raw))
            real_connect = sqlite3.connect
            reads = []
            def metadata_only(*args, **kwargs):
                db = real_connect(*args, **kwargs)
                def authorize(action, table, column, database, source):
                    if action == sqlite3.SQLITE_READ:
                        reads.append(table)
                        return sqlite3.SQLITE_OK if table == 'sqlite_master' else sqlite3.SQLITE_DENY
                    return sqlite3.SQLITE_OK
                # The in-memory reference builds indexes, which SQLite also
                # authorizes as column reads; only actual files must be metadata-only.
                if args[0] != ':memory:':
                    db.set_authorizer(authorize)
                return db
            with patch.object(sqlite3, 'connect', metadata_only):
                result = checks.storage(packet, intent, {'src/startup_gate.py': checks.sha(code)})
            self.assertEqual(identities, result['existing_db_identities'])
            self.assertTrue(reads)
            self.assertEqual({'sqlite_master'}, set(reads))
            self.assertNotIn('PUBLIC_SENTINEL', json.dumps(result))
            self.assertTrue(all(set(row) == {'file_id', 'catalog_sha256'} for row in result['databases'].values()))

    def test_idle_rejects_unknown_and_inflight_work(self):
        value = dict(state='READY', active_jobs=0, queued_jobs=0,
                     restricted_activity=dict(available=True, stable=True, quiescent=True,
                         active_requests=0, active_applications=0, nonterminal_runs=0))
        checks.idle(value)
        for change in (dict(active_jobs=1), dict(queued_jobs=1), dict(state='STARTING'), dict(restricted_activity={})):
            with self.assertRaises(ValueError):
                checks.idle({**value, **change})


if __name__ == '__main__':
    unittest.main()
