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
        (self.seed/'.bridge').mkdir()
        (self.seed/'.bridge'/'validation.json').write_text(json.dumps({'schema':1,'command':'git diff --cached --check'}))
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
        errors=[]
        start=threading.Barrier(3)
        rendezvous=threading.Barrier(2)
        writes={
            Path(a['worktree']).resolve(): ('paper-a.txt','a-parallel\n'),
            Path(b['worktree']).resolve(): ('paper-b.txt','b-parallel\n'),
        }
        original_run=w._run_job_shell

        def fake_run(shell, command, cwd, timeout):
            # This test is about job-lock granularity, not sandbox policy. Both
            # jobs must reach the runner together; a repository-wide lock would
            # strand the first caller at this barrier.
            rendezvous.wait(timeout=5)
            rel,value=writes[Path(cwd).resolve()]
            (Path(cwd)/rel).write_text(value)
            return 0,'',''

        def worker(job):
            try:
                start.wait()
                out=self.exec(job['job_id'], 'synthetic-parallel-run')
                if out['exit_code'] != 0:
                    raise AssertionError(f"workspace exec failed: {out!r}")
            except BaseException as exc:
                errors.append(exc)

        w._run_job_shell=fake_run
        try:
            t1=threading.Thread(target=worker,args=(a,))
            t2=threading.Thread(target=worker,args=(b,))
            t1.start(); t2.start(); start.wait(); t1.join(30); t2.join(30)
        finally:
            w._run_job_shell=original_run
        self.assertFalse(t1.is_alive()); self.assertFalse(t2.is_alive())
        self.assertEqual(errors, [])
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


    @unittest.skipIf(os.environ.get("_LOCAL_EXECUTOR_NESTED_VALIDATION") == "1", "requires direct process inspection outside the live v5 sandbox")
    def test_workspace_timeout_kills_descendant_and_marks_interrupted(self):
        j=self.create('research:paper:timeout')
        wt=Path(j['worktree']); marker=wt/'SURVIVED'
        child=wt/'child.py'; child.write_text("import time,pathlib\ntime.sleep(2)\npathlib.Path('SURVIVED').write_text('bad')\n")
        with self.assertRaisesRegex(w.WorkspaceError,'timed out'):
            w.exec_job(argparse.Namespace(state_dir=str(self.state),job=j['job_id'],command="python3 child.py & wait",timeout=1,shell='/bin/bash',no_checkpoint=False,checkpoint_message=None))
        import time as _time; _time.sleep(2.2)
        self.assertFalse(marker.exists())
        self.assertEqual(w.load_job(self.state,j['job_id'])['state'],'interrupted')

    def test_gc_is_frozen_by_default_and_preserves_every_artifact(self):
        j=self.create('research:paper:a')
        self.exec(j['job_id'],"printf 'a1\\n' > paper-a.txt")
        self.ready(j['job_id']); self.integrate(j['job_id'])
        p=w.job_path(self.state,j['job_id'])
        branch=j['branch']
        before=r('git','-C',str(self.repo),'ls-remote','origin',f'refs/heads/{branch}').stdout
        with self.assertRaisesRegex(w.WorkspaceError,'frozen'):
            w.gc_jobs(argparse.Namespace(state_dir=str(self.state),repo=None,retention_days=0,delete_remote_branch=True))
        self.assertTrue(p.exists())
        self.assertTrue(Path(j['worktree']).exists())
        self.assertEqual(r('git','-C',str(self.repo),'ls-remote','origin',f'refs/heads/{branch}').stdout,before)

    def test_gc_maintenance_preserves_later_unintegrated_remote_commit(self):
        j=self.create('research:paper:a')
        self.exec(j['job_id'],"printf 'a1\\n' > paper-a.txt")
        self.ready(j['job_id']); self.integrate(j['job_id'])
        p=w.job_path(self.state,j['job_id'])
        meta=json.loads(p.read_text());meta['integrated_at']='2000-01-01T00:00:00Z';p.write_text(json.dumps(meta))
        wt=Path(j['worktree']); (wt/'later.txt').write_text('unintegrated')
        r('git','-C',str(wt),'add','later.txt');r('git','-C',str(wt),'commit','-qm','later unique work')
        r('git','-C',str(wt),'push','origin',f"HEAD:refs/heads/{j['branch']}")
        tip=r('git','-C',str(wt),'rev-parse','HEAD').stdout.strip()
        out=w.gc_jobs(argparse.Namespace(state_dir=str(self.state),repo=None,retention_days=1,delete_remote_branch=True,enable_maintenance_gc=True))
        self.assertNotIn(j['job_id'],out)
        self.assertTrue(p.exists());self.assertTrue(wt.exists())
        self.assertEqual(r('git','-C',str(self.repo),'ls-remote','origin',f"refs/heads/{j['branch']}").stdout.split()[0],tip)

    def test_gc_maintenance_refuses_locked_worktree_and_keeps_metadata(self):
        j=self.create('research:paper:a')
        self.exec(j['job_id'],"printf 'a1\\n' > paper-a.txt")
        self.ready(j['job_id']); self.integrate(j['job_id'])
        p=w.job_path(self.state,j['job_id'])
        meta=json.loads(p.read_text());meta['integrated_at']='2000-01-01T00:00:00Z';p.write_text(json.dumps(meta))
        out=w.gc_jobs(argparse.Namespace(state_dir=str(self.state),repo=None,retention_days=1,delete_remote_branch=True,enable_maintenance_gc=True))
        self.assertNotIn(j['job_id'],out)
        self.assertTrue(p.exists());self.assertTrue(Path(j['worktree']).exists())

    def test_gc_maintenance_failed_worktree_removal_keeps_remote_and_metadata(self):
        from unittest.mock import patch
        j=self.create('research:paper:failure')
        self.exec(j['job_id'],"printf 'a1\n' > paper-a.txt")
        self.ready(j['job_id']);self.integrate(j['job_id'])
        meta_path=w.job_path(self.state,j['job_id'])
        meta=json.loads(meta_path.read_text());meta['integrated_at']='2000-01-01T00:00:00Z';meta_path.write_text(json.dumps(meta))
        ref=f"refs/heads/{j['branch']}"
        expected=r('git','-C',str(self.repo),'ls-remote','origin',ref).stdout
        old_git=w.git
        def fail_worktree_removal(cwd,*args,**kw):
            if args[:2]==('worktree','remove'):
                return subprocess.CompletedProcess(['git',*args],1,'','simulated removal failure')
            return old_git(cwd,*args,**kw)
        with patch.object(w,'git',side_effect=fail_worktree_removal):
            out=w.gc_jobs(argparse.Namespace(state_dir=str(self.state),repo=None,retention_days=1,delete_remote_branch=True,enable_maintenance_gc=True))
        self.assertNotIn(j['job_id'],out)
        self.assertTrue(meta_path.exists());self.assertTrue(Path(j['worktree']).exists())
        self.assertEqual(r('git','-C',str(self.repo),'ls-remote','origin',ref).stdout,expected)

    def test_gc_maintenance_remote_lease_rejects_concurrent_branch_advance(self):
        from unittest.mock import patch
        j=self.create('research:paper:lease')
        self.exec(j['job_id'],"printf 'a1\n' > paper-a.txt")
        self.ready(j['job_id']);self.integrate(j['job_id'])
        meta_path=w.job_path(self.state,j['job_id'])
        meta=json.loads(meta_path.read_text());meta['integrated_at']='2000-01-01T00:00:00Z';meta_path.write_text(json.dumps(meta))
        ref=f"refs/heads/{j['branch']}"
        old_remote_ref=w._remote_ref
        once=[True];new_sha=[None]
        def advance_during_gc(repo,remote,branch):
            original=old_remote_ref(repo,remote,branch)
            if once[0]:
                once[0]=False
                clone=self.root/'gc-concurrent-clone'
                r('git','clone','-q',str(self.origin),str(clone))
                r('git','-C',str(clone),'config','user.name','Concurrent')
                r('git','-C',str(clone),'config','user.email','other@example.invalid')
                r('git','-C',str(clone),'checkout','-q','-b','advance',original)
                (clone/'new-unique.txt').write_text('remote newer\n')
                r('git','-C',str(clone),'add','new-unique.txt')
                r('git','-C',str(clone),'commit','-qm','concurrent unique commit')
                new_sha[0]=r('git','-C',str(clone),'rev-parse','HEAD').stdout.strip()
                r('git','-C',str(clone),'push','origin',f'HEAD:{ref}')
            return original
        with patch.object(w,'_remote_ref',side_effect=advance_during_gc):
            out=w.gc_jobs(argparse.Namespace(state_dir=str(self.state),repo=None,retention_days=1,delete_remote_branch=True,enable_maintenance_gc=True))
        self.assertNotIn(j['job_id'],out)
        self.assertTrue(meta_path.exists())
        self.assertEqual(r('git','-C',str(self.repo),'ls-remote','origin',ref).stdout.split()[0],new_sha[0])

if __name__=='__main__': unittest.main(verbosity=2)
