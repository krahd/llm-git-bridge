from __future__ import annotations
import argparse
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import workspace as w


def git(*args,cwd=None):
    return subprocess.run(['git',*args],cwd=cwd,check=True,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE).stdout.strip()


class FinishTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name); seed=self.root/'seed'
        git('init','-q','-b','main',str(seed));git('-C',str(seed),'config','user.name','Test');git('-C',str(seed),'config','user.email','test@example.invalid')
        (seed/'paper.txt').write_text('original\n')
        (seed/'.bridge').mkdir()
        (seed/'.bridge'/'validation.json').write_text(json.dumps({'schema':1,'command':'git diff --cached --check'}))
        git('-C',str(seed),'add','.');git('-C',str(seed),'commit','-qm','initial')
        self.bare=self.root/'origin.git';self.repo=self.root/'repo';self.state=self.root/'state'
        git('clone','-q','--bare',str(seed),str(self.bare));git('clone','-q',str(self.bare),str(self.repo))
        git('-C',str(self.repo),'config','user.name','Test');git('-C',str(self.repo),'config','user.email','test@example.invalid')
        self.job=w.create_job(argparse.Namespace(state_dir=str(self.state),repo=str(self.repo),resource='papers/test',remote='origin',target='main',job_id='finish-test-job',worktree_root=str(self.root/'worktrees'),push_initial=True))
        self.wt=Path(self.job['worktree'])
        (self.wt/'paper.txt').write_text('changed\n')

    def args(self):
        return argparse.Namespace(state_dir=str(self.state),job=self.job['job_id'],message='Finish test',validate=None,timeout=60,shell='/bin/bash')

    def test_finish_publishes_validated_commit_and_immutable_receipt(self):
        result=w.finish_job(self.args())
        self.assertEqual(result['job']['state'],'integrated')
        receipt=result['receipt']
        self.assertEqual(receipt['checkpoint'],git('-C',str(self.wt),'rev-parse','HEAD'))
        self.assertEqual(receipt['integration_commit'],git('-C',str(self.repo),'ls-remote','origin','refs/heads/main').split()[0])
        self.assertTrue(receipt['policy_sha256']);self.assertFalse(receipt['legacy_unverified'])
        self.assertEqual(w.finish_job(self.args())['receipt'],receipt)
        self.assertTrue(self.wt.exists(), 'finish never performs garbage collection')
        # A subsequent legitimate edit does not invalidate this historical receipt.
        git('-C',str(self.repo),'pull','-q','--ff-only','origin','main')
        (self.repo/'paper.txt').write_text('later independent change\n')
        git('-C',str(self.repo),'add','paper.txt');git('-C',str(self.repo),'commit','-qm','later')
        git('-C',str(self.repo),'push','-q','origin','main')
        self.assertEqual(w.finish_job(self.args())['receipt'],receipt)

    def test_missing_trusted_validator_blocks_and_preserves_job(self):
        # Advance canonical branch without validation policy.
        (self.repo/'.bridge'/'validation.json').unlink()
        git('-C',str(self.repo),'add','-A');git('-C',str(self.repo),'commit','-qm','remove validator')
        git('-C',str(self.repo),'push','-q','origin','main')
        with self.assertRaisesRegex(w.WorkspaceError,'missing trusted canonical validation'):
            w.finish_job(self.args())
        self.assertTrue(self.wt.exists())
        self.assertFalse(w._receipt_file(self.state,self.job['job_id']).exists())

    def test_candidate_cannot_weaken_its_own_validator(self):
        (self.wt/'.bridge'/'validation.json').write_text('{"schema":1,"command":"true"}\n')
        with self.assertRaisesRegex(w.WorkspaceError,'changed the trusted validation policy'):
            w.finish_job(self.args())
        self.assertTrue(self.wt.exists())

    def test_push_then_receipt_failure_reconciles_without_duplicate_commit(self):
        real=w._write_terminal_receipt
        fail=[True]
        def maybe_fail(*args,**kwargs):
            if fail[0]:
                fail[0]=False
                raise w.WorkspaceError('injected lost receipt after verified remote push')
            return real(*args,**kwargs)
        with patch.object(w,'_write_terminal_receipt',side_effect=maybe_fail):
            with self.assertRaisesRegex(w.WorkspaceError,'injected lost receipt'):
                w.finish_job(self.args())
        before=git('-C',str(self.repo),'ls-remote','origin','refs/heads/main').split()[0]
        receipt=w.finish_job(self.args())['receipt']
        after=git('-C',str(self.repo),'ls-remote','origin','refs/heads/main').split()[0]
        self.assertEqual(before,after)
        self.assertEqual(receipt['integration_commit'],before)

if __name__=='__main__':unittest.main()
