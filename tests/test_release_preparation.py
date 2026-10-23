"""Public release/migration fixtures. No production data or service operations."""
import hashlib,importlib.util,json,os,runpy,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import slack_gateway
REPO=Path(__file__).resolve().parents[1]
def load(name):
    spec=importlib.util.spec_from_file_location(name,REPO/'deployment'/(name+'.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
db=load('release_db');entry=load('gateway_entry')

class ReleasePreparationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
    def old_store(self):
        store=slack_gateway.RestrictedStore(self.root)
        with store.db() as conn:
            conn.execute('DROP TABLE retry_links')
            conn.execute("INSERT INTO runs VALUES (?,?,?,?,?,?)",('public_req', 'a'*64,'interrupted',
                '{"status":"interrupted","retry_safe":false}','public_session','chat'))
        return store
    def test_all_tables_include_sqlite_sequence_and_typed_values(self):
        path=self.root/'fixture.db'
        conn=sqlite3.connect(path)
        conn.executescript('CREATE TABLE fixture(id INTEGER PRIMARY KEY AUTOINCREMENT,v);')
        conn.executemany('INSERT INTO fixture(v) VALUES(?)',[(None,),(1,),('1',),(1.0,),(b'1',)])
        conn.commit();conn.close()
        before=db.snapshot_file(path)
        self.assertIn('sqlite_sequence',before['tables'])
        self.assertEqual(before['tables']['fixture']['rows'],5)
        self.assertEqual(len({db.digest(db.cell(v)) for v in (None,1,'1',1.0,b'1')}),5)
        self.assertEqual(before,db.snapshot_file(path))
    def test_additive_migration_preserves_every_existing_table_and_version(self):
        store=self.old_store();before=db.snapshot_file(store.path)
        result=db.migrate(self.root,before);after=db.snapshot_file(store.path)
        self.assertTrue(result['existing_rows_unchanged'])
        self.assertEqual(set(after['tables'])-set(before['tables']),{'retry_links'})
        for name,table in before['tables'].items():self.assertEqual(after['tables'][name],table)
        self.assertEqual(after['user_version'],before['user_version'])
        conn=sqlite3.connect(store.path);db.verify_retry_table(conn);conn.close()
    def test_known_migrated_schema_is_a_noop(self):
        store=self.old_store();db.migrate(self.root,db.snapshot_file(store.path))
        before=db.snapshot_file(store.path)
        self.assertFalse(db.migrate(self.root,before)['created'])
        self.assertEqual(before,db.snapshot_file(store.path))
    def test_baseline_drift_refuses_ddl(self):
        store=self.old_store();before=db.snapshot_file(store.path)
        conn=sqlite3.connect(store.path);conn.execute("UPDATE runs SET operation='review'");conn.commit();conn.close()
        with self.assertRaises(ValueError):db.migrate(self.root,before)
        self.assertNotIn('retry_links',db.snapshot_file(store.path)['tables'])
    def test_bad_existing_retry_schema_is_not_repaired_or_deleted(self):
        store=self.old_store()
        conn=sqlite3.connect(store.path);conn.execute('CREATE TABLE retry_links(unknown TEXT)');conn.commit();conn.close()
        before=db.snapshot_file(store.path)
        with self.assertRaises(ValueError):db.migrate(self.root,before)
        self.assertEqual(before,db.snapshot_file(store.path))
    def test_failed_post_ddl_check_rolls_back_only_its_transaction(self):
        store=self.old_store();before=db.snapshot_file(store.path)
        with patch.object(db,'verify_retry_table',side_effect=ValueError('PUBLIC failure')):
            with self.assertRaises(ValueError):db.migrate(self.root,before)
        self.assertEqual(before,db.snapshot_file(store.path))
    def test_missing_database_is_not_initialized(self):
        with self.assertRaises(FileNotFoundError):db.snapshot_file(self.root/'absent.db')
        self.assertFalse((self.root/'absent.db').exists())
    def make_release(self):
        release=self.root/'release';data=self.root/'data';release.mkdir();data.mkdir()
        files=[]
        for name in ('src/control_gateway.py','src/slack_gateway.py','src/site_settings.py','Manage-LocalBrain.ps1','Site-Settings.ps1'):
            path=release/name;path.parent.mkdir(exist_ok=True);path.write_text('PUBLIC fixed fixture')
            files.append(dict(path=name,sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
        manifest=dict(source_commit='a'*40,files=files)
        raw=json.dumps(manifest).encode();(release/'release-manifest.json').write_bytes(raw)
        pointer=self.root/'pointer.json'
        pointer.write_text(json.dumps(dict(schema='localbrain.gateway-release-pointer.v1',source_commit='a'*40,
            release_root=str(release),data_root=str(data),manifest_sha256=hashlib.sha256(raw).hexdigest())))
        return pointer,release,data
    def test_verified_release_and_separate_stable_data(self):
        pointer,release,data=self.make_release()
        info,observed,stable=entry.verify(pointer)
        self.assertEqual((observed,stable),(release,data))
        self.assertEqual(info['source_commit'],'a'*40)
    def test_modified_release_bytes_are_refused(self):
        pointer,release,data=self.make_release();(release/'src/slack_gateway.py').write_text('changed')
        with self.assertRaises(ValueError):entry.verify(pointer)
    def test_manifest_drift_is_refused(self):
        pointer,release,data=self.make_release();(release/'release-manifest.json').write_text('{}')
        with self.assertRaises(ValueError):entry.verify(pointer)
    def test_data_root_never_defaults_to_release_when_explicit(self):
        data=self.root/'data';data.mkdir()
        with patch.dict(os.environ,{'LOCALBRAIN_DATA_ROOT':str(data)}):
            settings=runpy.run_path(str(REPO/'src/site_settings.py'))
        self.assertEqual(settings['ROOT'],data)
        self.assertEqual(settings['CODE_ROOT'],REPO)
    def test_relative_data_root_is_refused(self):
        with patch.dict(os.environ,{'LOCALBRAIN_DATA_ROOT':'relative'}):
            with self.assertRaises(ValueError):runpy.run_path(str(REPO/'src/site_settings.py'))

    def test_existing_retry_trigger_is_not_qualified_as_noop(self):
        store=self.old_store()
        conn=sqlite3.connect(store.path);conn.execute(db.RETRY_DDL)
        conn.execute("CREATE TRIGGER unexpected AFTER INSERT ON retry_links BEGIN UPDATE runs SET operation='review'; END")
        conn.commit();conn.close();before=db.snapshot_file(store.path)
        with self.assertRaises(ValueError):db.migrate(self.root,before)
        self.assertEqual(before,db.snapshot_file(store.path))
    def test_existing_retry_extra_check_is_refused(self):
        store=self.old_store()
        conn=sqlite3.connect(store.path);conn.execute(db.RETRY_DDL[:-1]+', CHECK(attempt_index<3))')
        conn.commit();conn.close();before=db.snapshot_file(store.path)
        with self.assertRaises(ValueError):db.migrate(self.root,before)
        self.assertEqual(before,db.snapshot_file(store.path))
    def test_existing_partial_extra_unique_index_is_refused(self):
        store=self.old_store()
        conn=sqlite3.connect(store.path);conn.execute(db.RETRY_DDL)
        conn.execute('CREATE UNIQUE INDEX extra_retry ON retry_links(next_id) WHERE attempt_index>0')
        conn.commit();conn.close();before=db.snapshot_file(store.path)
        with self.assertRaises(ValueError):db.migrate(self.root,before)
        self.assertEqual(before,db.snapshot_file(store.path))

    def test_unlisted_release_module_is_refused(self):
        pointer,release,data=self.make_release();(release/'src/unlisted.py').write_text('PUBLIC unexpected')
        with self.assertRaises(ValueError):entry.verify(pointer)
