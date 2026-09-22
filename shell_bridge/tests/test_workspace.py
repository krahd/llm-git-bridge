from __future__ import annotations
import argparse
import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import workspace as w


def r(*a,cwd=None):
    return subprocess.run(list(a),cwd=cwd,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True)


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.td=tempfile.TemporaryDirectory(); self.root=Path(self.td.name)
        self.seed=self.root/'seed'; self.origin=self.root/'origin.git'; self.repo=self.root/'repo'; self.state=self.root/'state'
        r('git','init','-q','-b','main',str(self.seed))
        r('git','-C',str(self.seed),'config','user.name','Test User'); r('git','-C',str(self.seed),'config','user.email','test@example.invalid')
        (self.seed/'paper-a.txt').write_text('a0\n'); (self.seed/'paper-b.txt').write_text('b0\n')
        r('git','-C',str(self.seed),'add','.'); r('git','-C',str(self.seed),'commit','-qm','initial')
        r('git','clone','-q','--bare',str(self.seed),str(self.origin)); r('git','clone','-q',str(self.origin),str(self.repo))
        r('git','-C',str(self.repo),'config','user.name','Test User'); r('git','-C',str(self.repo),'config','user.email','test@example.invalid')
    def tearDown(self): self.td.cleanup()
    def create(self,resource):
        return w.create_job(argparse.Namespace(state_dir=str(self.state),repo=str(self.repo),resource=resource,remote='origin',target='main',job_id=None,worktree_root=str(self.root/'worktrees'),push_initial=True))
    def exec(self,jid,command):
        return w.exec_job(argparse.Namespace(state_dir=str(self.state),job=jid,command=command,timeout=20,shell='/bin/bash',no_checkpoint=False,checkpoint_message=None))
    def ready(self,jid):
        return w.ready_job(argparse.Namespace(state_dir=str(self.state),job=jid,checkpoint=False,message=None))
    def integrate(self,jid):
        return w.integrate_job(argparse.Namespace(state_dir=str(self.state),job=jid,message=None,validate=None,timeout=30,shell='/bin/bash'))

    def test_two_jobs_same_repo_checkpoint_and_integrate_independently(self):
        a=self.create('research:paper:a'); b=self.create('research:paper:b')
        oa=self.exec(a['job_id'],"printf 'a1\\n' > paper-a.txt")
        ob=self.exec(b['job_id'],"printf 'b1\\n' > paper-b.txt")
        self.assertTrue(oa['checkpoint']); self.assertTrue(ob['checkpoint'])
        self.ready(a['job_id']); ia=self.integrate(a['job_id']); self.assertEqual(ia['state'],'integrated')
        # b has a different resource and different path; it can integrate despite main moving.
        self.ready(b['job_id']); ib=self.integrate(b['job_id']); self.assertEqual(ib['state'],'integrated')
        fresh=self.root/'fresh'; r('git','clone','-q',str(self.origin),str(fresh))
        self.assertEqual((fresh/'paper-a.txt').read_text(),'a1\n'); self.assertEqual((fresh/'paper-b.txt').read_text(),'b1\n')

    def test_two_jobs_execute_concurrently_without_repo_wide_lock(self):
        a=self.create('research:parallel:a'); b=self.create('research:parallel:b')
        rendezvous=self.root/'rendezvous'; rendezvous.mkdir()
        errors=[]
        start=threading.Barrier(3)
        def worker(job, path, value, marker, peer):
            try:
                start.wait()
                command=(
                    f"marker_dir={str(rendezvous)!r}; "
                    f"me=\"$marker_dir/{marker}\"; peer=\"$marker_dir/{peer}\"; "
                    "printf 'ready\n' > \"$me\"; "
                    "i=0; while [ ! -e \"$peer\" ]; do i=$((i+1)); "
                    "[ \"$i\" -lt 500 ] || exit 90; sleep 0.02; done; "
                    f"printf '%s\n' {value!r} > {path!r}"
                )
                out=self.exec(job['job_id'], command)
                if out['exit_code'] != 0:
                    raise AssertionError(
                        f"workspace exec failed for {marker}: rc={out['exit_code']} stderr={out['stderr']!r}"
                    )
            except BaseException as exc:
                errors.append(exc)
        t1=threading.Thread(target=worker,args=(a,'paper-a.txt','a-parallel','a.ready','b.ready'))
        t2=threading.Thread(target=worker,args=(b,'paper-b.txt','b-parallel','b.ready','a.ready'))
        t1.start(); t2.start(); start.wait(); t1.join(30); t2.join(30)
        self.assertFalse(t1.is_alive()); self.assertFalse(t2.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual((rendezvous/'a.ready').read_text(),'ready\n')
        self.assertEqual((rendezvous/'b.ready').read_text(),'ready\n')
        self.assertEqual((Path(a['worktree'])/'paper-a.txt').read_text(),'a-parallel\n')
        self.assertEqual((Path(b['worktree'])/'paper-b.txt').read_text(),'b-parallel\n')

    def test_ensure_job_is_idempotent_for_same_conversation_job_id(self):
        args=argparse.Namespace(state_dir=str(self.state),repo=str(self.repo),resource='research:ensure',remote='origin',target='main',job_id='conversation-stable-id',worktree_root=str(self.root/'worktrees'),push_initial=True)
        first=w.ensure_job(args)
        second=w.ensure_job(args)
        self.assertEqual(first['job_id'], second['job_id'])
        self.assertEqual(first['worktree'], second['worktree'])
        self.assertEqual(first['last_remote_checkpoint'], second['last_remote_checkpoint'])
        self.assertTrue(Path(first['worktree']).is_dir())
        jobs=[j for j in w.list_jobs(argparse.Namespace(state_dir=str(self.state),repo=None)) if j['job_id']=='conversation-stable-id']
        self.assertEqual(len(jobs),1)

    def test_ensure_job_rejects_identity_mismatch(self):
        args=argparse.Namespace(state_dir=str(self.state),repo=str(self.repo),resource='research:ensure:a',remote='origin',target='main',job_id='conversation-stable-id',worktree_root=str(self.root/'worktrees'),push_initial=True)
        w.ensure_job(args)
        args.resource='research:ensure:b'
        with self.assertRaisesRegex(w.WorkspaceError,'does not match'):
            w.ensure_job(args)

    def test_same_resource_requires_reconcile(self):
        a=self.create('research:paper:a'); b=self.create('research:paper:a')
        self.exec(a['job_id'],"printf 'a1\\n' > paper-a.txt")
        self.exec(b['job_id'],"printf 'a2\\n' >> paper-b.txt")
        self.ready(a['job_id']); self.integrate(a['job_id'])
        self.ready(b['job_id'])
        with self.assertRaisesRegex(w.WorkspaceError,'reconcile_required'):
            self.integrate(b['job_id'])
        jb=w.load_job(self.state,b['job_id']); self.assertEqual(jb['state'],'conflicted'); self.assertTrue(jb['resource_overlap'])

    def test_path_overlap_catches_different_resource_keys(self):
        a=self.create('research:x'); b=self.create('research:y')
        self.exec(a['job_id'],"printf 'one\\n' > paper-a.txt")
        self.exec(b['job_id'],"printf 'two\\n' > paper-a.txt")
        self.ready(a['job_id']); self.integrate(a['job_id']); self.ready(b['job_id'])
        with self.assertRaisesRegex(w.WorkspaceError,'reconcile_required'):
            self.integrate(b['job_id'])
        jb=w.load_job(self.state,b['job_id']); self.assertIn('paper-a.txt',jb['overlap_paths'])

    def test_mark_reconciled_requires_canonical_target_in_job_history(self):
        j=self.create('research:paper:a')
        # Advance canonical main independently after the job was created.
        (self.repo/'paper-b.txt').write_text('b-main\n')
        r('git','-C',str(self.repo),'add','paper-b.txt'); r('git','-C',str(self.repo),'commit','-qm','advance main'); r('git','-C',str(self.repo),'push','-q','origin','main')
        args=argparse.Namespace(state_dir=str(self.state),job=j['job_id'],target=None)
        with self.assertRaisesRegex(w.WorkspaceError,'reconciliation not proven'):
            w.mark_reconciled(args)
        wt=Path(j['worktree'])
        r('git','-C',str(wt),'fetch','-q','origin','main')
        r('git','-C',str(wt),'merge','-q','--no-edit','origin/main')
        r('git','-C',str(wt),'push','-q','origin',f"HEAD:refs/heads/{j['branch']}")
        out=w.mark_reconciled(args)
        self.assertEqual(out['reconciled_target'], r('git','-C',str(self.repo),'rev-parse','origin/main').stdout.strip())

    def test_recover_reconstructs_missing_metadata(self):
        j=self.create('research:paper:a'); p=w.job_path(self.state,j['job_id']); p.unlink()
        out=w.recover_jobs(argparse.Namespace(state_dir=str(self.state),repo=str(self.repo),remote='origin',target='main'))
        rec=[x for x in out if x['job_id']==j['job_id']][0]
        self.assertEqual(rec['state'],'interrupted'); self.assertTrue(rec['resource'].startswith('repo:'))


    def test_workspace_timeout_kills_descendant_and_marks_interrupted(self):
        j=self.create('research:paper:timeout')
        wt=Path(j['worktree']); marker=wt/'SURVIVED'
        child=wt/'child.py'; child.write_text("import time,pathlib\ntime.sleep(2)\npathlib.Path('SURVIVED').write_text('bad')\n")
        with self.assertRaisesRegex(w.WorkspaceError,'timed out'):
            w.exec_job(argparse.Namespace(state_dir=str(self.state),job=j['job_id'],command="python3 child.py & wait",timeout=1,shell='/bin/bash',no_checkpoint=False,checkpoint_message=None))
        import time as _time; _time.sleep(2.2)
        self.assertFalse(marker.exists())
        self.assertEqual(w.load_job(self.state,j['job_id'])['state'],'interrupted')

    def test_gc_can_remove_older_integrated_job_after_main_advances(self):
        a=self.create('research:paper:a'); self.exec(a['job_id'],"printf 'a1\\n' > paper-a.txt"); self.ready(a['job_id']); self.integrate(a['job_id'])
        b=self.create('research:paper:b'); self.exec(b['job_id'],"printf 'b1\\n' > paper-b.txt"); self.ready(b['job_id']); self.integrate(b['job_id'])
        p=w.job_path(self.state,a['job_id']); meta=json.loads(p.read_text()); meta['integrated_at']='2000-01-01T00:00:00Z'; p.write_text(json.dumps(meta))
        removed=w.gc_jobs(argparse.Namespace(state_dir=str(self.state),repo=None,retention_days=1,delete_remote_branch=False))
        self.assertIn(a['job_id'],removed)

    def test_gc_refuses_dirty_integrated_worktree(self):
        j=self.create('research:paper:a'); self.exec(j['job_id'],"printf 'a1\\n' > paper-a.txt"); self.ready(j['job_id']); ij=self.integrate(j['job_id'])
        p=w.job_path(self.state,j['job_id']); meta=json.loads(p.read_text()); meta['integrated_at']='2000-01-01T00:00:00Z'; p.write_text(json.dumps(meta))
        Path(j['worktree'],'LOCAL').write_text('dirty')
        removed=w.gc_jobs(argparse.Namespace(state_dir=str(self.state),repo=None,retention_days=1,delete_remote_branch=False))
        self.assertNotIn(j['job_id'],removed); self.assertTrue(p.exists())

if __name__=='__main__': unittest.main(verbosity=2)
