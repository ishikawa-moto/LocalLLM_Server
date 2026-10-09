"""CPU embeddings through localhost llama.cpp with a portable SQLite vector store."""
from __future__ import annotations
from site_settings import LOOPBACK, site_port

import array
import contextlib
import http.client
import json
import math
import sqlite3
import threading
import time
from pathlib import Path


class SemanticIndex:
    def __init__(self, root: Path, settings: dict):
        self.root=Path(root); self.settings=settings; self.enabled=bool(settings.get('semantic_enabled',False))
        self.dimension=int(settings.get('embedding_dimensions',512)); self.batch_size=int(settings.get('embedding_batch_size',8))
        self.endpoint=settings.get('embedding_endpoint',('http://' + LOOPBACK + ':' + str(site_port('embedding')))); self.model_name=settings.get('embedding_model','qwen3-embedding-0.6b-q8')
        self.db=self.root/'runtime'/'semantic.sqlite3'; self._lock=threading.RLock(); self.error=None
        self.db.parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as connection:
            connection.execute('CREATE TABLE IF NOT EXISTS vectors (chunk_id TEXT PRIMARY KEY, path TEXT NOT NULL, revision TEXT NOT NULL, dimension INTEGER NOT NULL, vector BLOB NOT NULL)')

    @contextlib.contextmanager
    def connect(self):
        connection=sqlite3.connect(self.db,timeout=30)
        try:
            with connection: yield connection
        finally: connection.close()
    def status(self):
        with self.connect() as connection: count=connection.execute('SELECT COUNT(*) FROM vectors').fetchone()[0]
        return {'enabled':self.enabled,'ready':self.enabled and self.error is None,'backend':'sqlite-vector','model':self.model_name,
                'dimensions':self.dimension,'endpoint':self.endpoint,'vectors':count,'error':self.error}

    def _wait_ready(self,timeout=90):
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            try:
                connection=http.client.HTTPConnection(LOOPBACK,site_port('embedding'),timeout=2); connection.request('GET','/health')
                ready=connection.getresponse().status==200; connection.close()
                if ready:return True
            except OSError: pass
            time.sleep(.5)
        return False

    def encode(self,texts):
        if not self._wait_ready(): raise ConnectionError('llama.cpp embedding service is not ready')
        body=json.dumps({'model':self.model_name,'input':texts,'encoding_format':'float'}).encode()
        connection=http.client.HTTPConnection(LOOPBACK,site_port('embedding'),timeout=900)
        connection.request('POST','/v1/embeddings',body=body,headers={'Content-Type':'application/json','Content-Length':str(len(body))})
        response=connection.getresponse(); raw=response.read(); connection.close()
        if response.status!=200: raise RuntimeError(f'embedding endpoint returned HTTP {response.status}')
        data=sorted(json.loads(raw)['data'],key=lambda item:item['index']); result=[]
        for item in data:
            values=[float(value) for value in item['embedding'][:self.dimension]]
            norm=math.sqrt(sum(value*value for value in values)) or 1.0; result.append([value/norm for value in values])
        return result

    @staticmethod
    def pack(values): return array.array('f',values).tobytes()
    @staticmethod
    def unpack(value):
        result=array.array('f'); result.frombytes(value); return result

    def sync(self,rows):
        if not self.enabled:return {'enabled':False,'indexed':0,'removed':0,'degraded':False,'error':None}
        with self._lock,self.connect() as connection:
            old={row[0]:row[1] for row in connection.execute('SELECT chunk_id,revision FROM vectors')}; current={row['id']:row['revision'] for row in rows}
            changed=[row for row in rows if old.get(row['id'])!=row['revision']]; removed=set(old)-set(current)
            try:
                for start in range(0,len(changed),self.batch_size):
                    batch=changed[start:start+self.batch_size]; vectors=self.encode([row['text'] for row in batch])
                    connection.executemany('INSERT OR REPLACE INTO vectors VALUES (?,?,?,?,?)',[(row['id'],row['path'],row['revision'],len(vector),self.pack(vector)) for row,vector in zip(batch,vectors)])
                    connection.commit()
                if removed:
                    connection.executemany('DELETE FROM vectors WHERE chunk_id=?',[(item,) for item in removed]); connection.commit()
                self.error=None; return {'enabled':True,'indexed':len(changed),'removed':len(removed),'degraded':False,'error':None}
            except Exception as exc:
                self.error=f'{type(exc).__name__}: {exc}'; return {'enabled':True,'indexed':0,'removed':0,'degraded':True,'error':self.error}

    def search(self,query,limit):
        if not self.enabled:return []
        try: needle=self.encode([query])[0]
        except Exception as exc: self.error=f'{type(exc).__name__}: {exc}'; return []
        scores=[]
        with self.connect() as connection:
            for chunk_id,blob in connection.execute('SELECT chunk_id,vector FROM vectors'):
                vector=self.unpack(blob); score=sum(left*right for left,right in zip(needle,vector)); scores.append((score,chunk_id))
        scores.sort(reverse=True); self.error=None
        return [{'chunk_id':chunk_id,'semantic_score':score} for score,chunk_id in scores[:limit]]
