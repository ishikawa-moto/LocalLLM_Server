import importlib.util
import pathlib
import sys
import tempfile
import unittest

SRC=pathlib.Path(__file__).resolve().parents[1]/'src'/'semantic_index.py'
spec=importlib.util.spec_from_file_location('semantic_index_test',SRC); module=importlib.util.module_from_spec(spec); sys.modules[spec.name]=module; spec.loader.exec_module(module)


class SemanticTests(unittest.TestCase):
    def test_differential_sync_delete_and_search(self):
        with tempfile.TemporaryDirectory(prefix='semantic-test-') as temp:
            index=module.SemanticIndex(pathlib.Path(temp),{'semantic_enabled':True,'embedding_dimensions':2,'embedding_batch_size':8})
            def encode(texts):
                return [[1.0,0.0] if 'alpha' in text else [0.0,1.0] for text in texts]
            index.encode=encode
            rows=[{'id':'a'*64,'path':'wiki/a.md','revision':'r1','text':'alpha'}, {'id':'b'*64,'path':'wiki/b.md','revision':'r1','text':'beta'}]
            first=index.sync(rows); second=index.sync(rows)
            self.assertEqual(first['indexed'],2); self.assertEqual(second['indexed'],0)
            self.assertEqual(index.search('alpha',1)[0]['chunk_id'],'a'*64)
            removed=index.sync(rows[:1]); self.assertEqual(removed['removed'],1); self.assertEqual(index.status()['vectors'],1)


if __name__=='__main__': unittest.main()
