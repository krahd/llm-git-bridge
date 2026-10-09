from __future__ import annotations
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import bridge as b

class LogicalOperationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.state=self.root/'state'
        self.cfg={'state_dir':str(self.state),'allowed_root':str(self.root),'max_timeout_seconds':60}
        self.requests={}
        self.execute_count=0
        self.lock=threading.Lock()

    def payload(self,id,command='printf safe',operation_id='one-logical-work'):
        req={'protocol':1,'id':id,'cwd':str(self.root),'command':command,
             'explanation':'Test unique logical execution', 'timeout_seconds':30,'write_scope':'read_only',
             'operation_id':operation_id}
        self.requests[id+'.json']=json.dumps(req).encode('utf8')

    def run_requests(self,ids):
        def copy(cfg,leaf,path):
            path.write_bytes(self.requests[leaf.split('/')[-1]])
        def persist(result,local,marker,name,cfg):
            b.atomic_write(local,json.dumps(result).encode())
            b.atomic_write(marker,json.dumps({'status':result['status'],'request_sha256':result['request_sha256']}).encode())
        def execute(name,cfg,raw):
            with self.lock:
                self.execute_count+=1
            time.sleep(0.04)
            id=name[:-5];d=self.state/'requests'/id;d.mkdir(parents=True,exist_ok=True)
            persist(b.result_envelope(id,b.sha256_bytes(raw),'completed',{'exit_code':0,'stdout_text':'ONCE'}),
                    d/'result.json',d/'finished.json',name,cfg)
        with patch.object(b,'_copy_request',side_effect=copy),\
             patch.object(b,'_persist_then_publish',side_effect=persist),\
             patch.object(b,'_process_one_request',side_effect=execute),\
             patch.object(b,'_publish_stored'):
            threads=[threading.Thread(target=b.process_one,args=(i+'.json',self.cfg)) for i in ids]
            for thread in threads:thread.start()
            for thread in threads:thread.join(5)
            self.assertTrue(all(not t.is_alive() for t in threads))

    def test_concurrent_different_transport_ids_run_once(self):
        self.payload('delivery-a');self.payload('delivery-b')
        self.run_requests(['delivery-a','delivery-b'])
        self.assertEqual(self.execute_count,1)
        outputs=[json.loads((self.state/'requests'/id/'result.json').read_text()) for id in ('delivery-a','delivery-b')]
        self.assertEqual([o['status'] for o in outputs],['completed','completed'])
        self.assertEqual(sum(bool(o.get('replayed')) for o in outputs),1)

    def test_late_original_cannot_execute_again(self):
        self.payload('replacement');self.payload('original')
        self.run_requests(['replacement'])
        self.run_requests(['original'])
        self.assertEqual(self.execute_count,1)
        result=json.loads((self.state/'requests'/'original'/'result.json').read_text())
        self.assertEqual(result['original_request_id'],'replacement')

    def test_different_payload_same_operation_is_rejected(self):
        self.payload('first');self.payload('second',command='printf CHANGED')
        self.run_requests(['first']);self.run_requests(['second'])
        self.assertEqual(self.execute_count,1)
        out=json.loads((self.state/'requests'/'second'/'result.json').read_text())
        self.assertEqual(out['status'],'rejected')
        self.assertIn('conflicting canonical payload',out['message'])

    def test_admitted_without_proven_finished_remains_indeterminate(self):
        self.payload('first');self.payload('second')
        self.run_requests(['first'])
        (self.state/'requests'/'first'/'finished.json').unlink()
        self.run_requests(['second'])
        self.assertEqual(self.execute_count,1)
        out=json.loads((self.state/'requests'/'second'/'result.json').read_text())
        self.assertEqual(out['status'],'indeterminate')

if __name__=='__main__':unittest.main()
