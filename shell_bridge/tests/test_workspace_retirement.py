"""Safety regressions for operator-only exact-job worktree lifecycle cleanup."""
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
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import workspace as w
import workspace_retirement as ret


def run(*args):
    return subprocess.run(args, capture_output=True, text=True, check=True)


class RetireTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.seed = self.root / "seed"
        self.origin = self.root / "origin.git"
        self.repo = self.root / "repo"
        self.state = self.root / "state"
        run("git", "init", "-q", "-b", "main", str(self.seed))
        run("git", "-C", str(self.seed), "config", "user.name", "Test")
        run("git", "-C", str(self.seed), "config", "user.email", "test@example.invalid")
        (self.seed / ".bridge").mkdir()
        (self.seed / ".bridge" / "validation.json").write_text('{"schema":1,"command":"git diff --cached --check"}\n')
        (self.seed / "file.txt").write_text("first\n")
        run("git", "-C", str(self.seed), "add", ".")
        run("git", "-C", str(self.seed), "commit", "-qm", "first")
        run("git", "clone", "-q", "--bare", str(self.seed), str(self.origin))
        run("git", "clone", "-q", str(self.origin), str(self.repo))
        run("git", "-C", str(self.repo), "config", "user.name", "Test")
        run("git", "-C", str(self.repo), "config", "user.email", "test@example.invalid")
        self.job = w.create_job(argparse.Namespace(state_dir=str(self.state), repo=str(self.repo),
                resource="paper:one", remote="origin", target="main", job_id="single-retire-001",
                worktree_root=None, push_initial=True))
        self.wt = Path(self.job["worktree"])
        (self.wt / "file.txt").write_text("second\n")
        w.exec_job(argparse.Namespace(state_dir=str(self.state),job=self.job["job_id"],command="true",
                timeout=30,shell="/bin/bash",no_checkpoint=False,checkpoint_message=None))
        w.ready_job(argparse.Namespace(state_dir=str(self.state),job=self.job["job_id"],
                checkpoint=False,message=None))
        w.integrate_job(argparse.Namespace(state_dir=str(self.state),job=self.job["job_id"],
                message=None,validate=None,timeout=40,shell="/bin/bash"))
        self.job = w.load_job(self.state, self.job["job_id"])
        self.receipt = w._receipt_file(self.state, self.job["job_id"])
        self.assertTrue(self.receipt.exists())
        jobdata=json.loads(w.job_path(self.state,self.job["job_id"]).read_text())
        jobdata["integrated_at"]="2000-01-01T00:00:00Z"
        w.job_path(self.state,self.job["job_id"]).write_text(json.dumps(jobdata))

    def tearDown(self):
        self.tmp.cleanup()

    def test_atomic_job_json_syncs_parent_directory(self):
        """A fsynced file without an fsynced rename is not a durable intent."""
        import stat
        observed = []
        original = os.fsync
        def spy(fd):
            observed.append(stat.S_ISDIR(os.fstat(fd).st_mode))
            return original(fd)
        target = self.state / "workspaces" / "jobs" / "sync-probe.json"
        with patch.object(w.os, "fsync", side_effect=spy):
            w.atomic_json(target, {"intent": "retiring"})
        self.assertEqual(json.loads(target.read_text()), {"intent": "retiring"})
        self.assertIn(False, observed)  # file fsync
        self.assertIn(True, observed)   # containing directory fsync

    def test_atomic_job_json_reports_ambiguous_directory_sync_failure(self):
        """Do not claim a durable intent if rename succeeded but dir sync failed."""
        import stat
        original = os.fsync
        def fail_directory(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError("simulated directory fsync failure")
            return original(fd)
        target = self.state / "workspaces" / "jobs" / "sync-failure.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        with patch.object(w.os, "fsync", side_effect=fail_directory):
            with self.assertRaisesRegex(w.WorkspaceError, "durability is unverified"):
                w.atomic_json(target, {"intent": "retiring"})
        # After os.replace the file may be visible, but must not be reported durable.
        self.assertTrue(target.exists())

    def test_new_immutable_receipt_syncs_parent_directory(self):
        """A receipt must remain discoverable after power loss, not just fsync its bytes."""
        import stat
        observed = []
        original = os.fsync
        def spy(fd):
            observed.append(stat.S_ISDIR(os.fstat(fd).st_mode))
            return original(fd)
        job = dict(self.job)
        job["job_id"] = "receipt-directory-fsync"
        with patch.object(w.os, "fsync", side_effect=spy):
            w._write_terminal_receipt(
                self.state, job, policy_rev=job["base_commit"],
                policy_sha="f" * 64, candidate_tree=job["base_commit"])
        self.assertTrue(w._receipt_file(self.state, job["job_id"]).is_file())
        self.assertIn(False, observed)
        self.assertIn(True, observed)

    def test_immutable_receipt_directory_sync_error_is_not_success(self):
        """A visible but unpersisted receipt must not count as completed."""
        import stat
        original = os.fsync
        def fail_directory(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError("simulated receipt directory sync failure")
            return original(fd)
        job = dict(self.job)
        job["job_id"] = "receipt-directory-failure"
        with patch.object(w.os, "fsync", side_effect=fail_directory):
            with self.assertRaisesRegex(w.WorkspaceError, "receipt durability is unverified"):
                w._write_terminal_receipt(
                    self.state, job, policy_rev=job["base_commit"],
                    policy_sha="f" * 64, candidate_tree=job["base_commit"])
        self.assertTrue(w._receipt_file(self.state, job["job_id"]).exists())

    def gc(self, **overrides):
        args=dict(state_dir=str(self.state),repo=str(self.repo),job=self.job["job_id"],
                  retention_days=14,allow_legacy=False,delete_remote_branch=False,
                  confirm_job=self.job["job_id"],enable_maintenance_gc=True)
        args.update(overrides)
        return w.gc_jobs(argparse.Namespace(**args))

    def test_lifecycle_status_identifies_integrated_backlog(self):
        info=w.lifecycle_status(argparse.Namespace(state_dir=str(self.state),repo=str(self.repo)))
        self.assertEqual(info["integrated_retained"],1)
        self.assertEqual(info["retirement_eligible_by_age"],1)
        self.assertEqual(info["released"],0)
        self.assertTrue(self.wt.exists())

    def test_remote_trusted_coordinator_exposes_inventory_but_not_gc(self):
        import importlib.util
        import types
        fake_ops=types.ModuleType("trusted_operations")
        fake_ops.TrustedOperationPolicyError=type("TrustedOperationPolicyError",(Exception,),{})
        fake_ops.registered_operation_from_config=lambda *a,**kw: None
        fake_ssh=types.ModuleType("ssh_pinned_helper")
        fake_ssh.SSHPolicyError=type("SSHPolicyError",(Exception,),{})
        fake_ssh.load_policy=lambda *a,**kw: None
        with patch.dict(sys.modules,{"trusted_operations":fake_ops,"ssh_pinned_helper":fake_ssh}):
            spec=importlib.util.spec_from_file_location("bridge_workspace_route_test",
                     Path(__file__).resolve().parents[1]/"bridge.py")
            module=importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        base=f"python3 {module.WORKSPACE_COORDINATOR}"
        self.assertTrue(module._trusted_workspace_coordinator(
            base+f" lifecycle --repo {self.repo}",self.root,self.state))
        self.assertFalse(module._trusted_workspace_coordinator(
            base+f" gc --repo {self.repo} --job {self.job['job_id']} --enable-maintenance-gc",
            self.root,self.state))
        self.assertFalse(module._trusted_workspace_coordinator(
            base+" lifecycle --repo /tmp/outside-root",self.root,self.state))

    def test_lifecycle_reports_orphaned_registered_workspace_without_deleting(self):
        orphan=self.root/".worktrees"/self.job["repo_id"]/"orphan-from-crash"
        w.git(self.repo,"worktree","add","-b","ai/workspace/orphan-from-crash",str(orphan))
        report=w.lifecycle_status(argparse.Namespace(state_dir=str(self.state),repo=str(self.repo)))
        self.assertEqual(report["registered_workspace_worktrees"],2)
        self.assertEqual(report["registered_orphans"],1)
        self.assertEqual(report["registered_locked"],1)
        self.assertTrue(orphan.exists())

    def test_retire_requires_operator_terminal_even_with_consent(self):
        with self.assertRaisesRegex(w.WorkspaceError,"local interactive operator"):
            self.gc()
        self.assertTrue(self.wt.exists())

    def test_retire_exact_job_works_with_verified_disposable_git_repo(self):
        # Isolated test only: deliberately mock out the Mac-only operator gate.
        with patch.object(ret,"_require_operator"), patch.object(ret,"_check_handles"):
            result=self.gc()
        self.assertTrue(result[0]["retired"])
        self.assertFalse(self.wt.exists())
        self.assertTrue(self.receipt.exists())
        self.assertTrue(w.job_path(self.state,self.job["job_id"]).exists())
        self.assertTrue(w.load_job(self.state,self.job["job_id"])["worktree_released_at"])
        self.assertEqual(run("git","-C",str(self.repo),"rev-parse",self.job["branch"]).stdout.strip(),
                         self.job["last_checkpoint"])
        self.assertEqual(run("git","-C",str(self.repo),"ls-remote","origin",
                             f"refs/heads/{self.job['branch']}").stdout.split()[0],
                         self.job["last_checkpoint"])
        status=w.lifecycle_status(argparse.Namespace(state_dir=str(self.state),repo=str(self.repo)))
        self.assertEqual(status["released"],1)

    def test_retire_repeated_execution_does_not_delete_other_jobs(self):
        with patch.object(ret,"_require_operator"), patch.object(ret,"_check_handles"):
            self.gc()
            with self.assertRaisesRegex(w.WorkspaceError,"already released"):
                self.gc()
        self.assertTrue(self.receipt.exists())

    def test_wrong_confirmation_cannot_delete(self):
        with patch.object(ret,"_require_operator"), patch.object(ret,"_check_handles"):
            with self.assertRaisesRegex(w.WorkspaceError,"confirm-job"):
                self.gc(confirm_job="anything-else")
        self.assertTrue(self.wt.exists())

    def test_never_remote_delete_including_explicit_request(self):
        with patch.object(ret,"_require_operator"), patch.object(ret,"_check_handles"):
            with self.assertRaisesRegex(w.WorkspaceError,"remote branch deletion prohibited"):
                self.gc(delete_remote_branch=True)
        self.assertTrue(self.wt.exists())

    def test_missing_receipt_fails_closed_even_if_integrated(self):
        self.receipt.unlink()
        with patch.object(ret,"_require_operator"), patch.object(ret,"_check_handles"):
            with self.assertRaisesRegex(w.WorkspaceError,"missing historical receipt"):
                self.gc()
        self.assertTrue(self.wt.exists())

    def test_modern_missing_receipt_refused_despite_legacy_optin(self):
        self.receipt.unlink()
        with patch.object(ret,"_require_operator"), patch.object(ret,"_check_handles"):
            with self.assertRaisesRegex(w.WorkspaceError,"modern integration unexpectedly missing"):
                self.gc(allow_legacy=True)
        self.assertTrue(self.wt.exists())

    def test_legacy_integrated_without_receipt_retires_in_one_verified_operation(self):
        """Use real legacy-format Git trailers, not a mock of remote proof."""
        run("git", "-C", str(self.repo), "pull", "-q", "--ff-only", "origin", "main")
        job = w.load_job(self.state, self.job["job_id"])
        message = ("Bridge-Job-ID: " + job["job_id"] + "\n" +
                   "Bridge-Resource: " + job["resource"] + "\n" +
                   "Bridge-Base: " + job["base_commit"] + "\n" +
                   "Bridge-Checkpoint: " + job["last_checkpoint"])
        run("git", "-C", str(self.repo), "commit", "-q", "--allow-empty",
            "-m", "Historical integration without policy trailers", "-m", message)
        run("git", "-C", str(self.repo), "push", "-q", "origin", "main")
        job["integration_commit"] = run("git", "-C", str(self.repo),
                                        "rev-parse", "HEAD").stdout.strip()
        w.save_job(self.state, job)
        self.receipt.unlink()
        with patch.object(ret, "_require_operator"), patch.object(ret, "_check_handles"):
            result = self.gc(allow_legacy=True)
        self.assertTrue(result[0]["retired"])
        self.assertFalse(self.wt.exists())
        self.assertFalse(self.receipt.exists())  # Never forge a legacy receipt.
        self.assertTrue(w.load_job(self.state, self.job["job_id"])["worktree_released_at"])
        self.assertEqual(run("git", "-C", str(self.repo), "ls-remote", "origin",
                             f"refs/heads/{job['branch']}").stdout.split()[0],
                         job["last_checkpoint"])

    def test_malformed_receipt_hard_blocker(self):
        self.receipt.write_text("this is not JSON")
        with patch.object(ret,"_require_operator"), patch.object(ret,"_check_handles"):
            with self.assertRaisesRegex(w.WorkspaceError,"invalid immutable integration receipt"):
                self.gc(allow_legacy=True)
        self.assertTrue(self.wt.exists())

    def test_ignored_file_preserved(self):
        (self.wt/".gitignore").write_text("secret.bin\n")
        (self.wt/"secret.bin").write_bytes(b"unrecorded")
        # .gitignore itself is untracked, which must already be sufficient.
        with patch.object(ret,"_require_operator"), patch.object(ret,"_check_handles"):
            with self.assertRaisesRegex(w.WorkspaceError,"dirty tracked or untracked"):
                self.gc()
        self.assertTrue((self.wt/"secret.bin").exists())

    def test_ignored_payload_in_existing_ignored_directory_preserved(self):
        (self.wt/"__pycache__").mkdir()
        (self.wt/"__pycache__"/"unique.wav").write_bytes(b"unique")
        # Ignore via .git/info/exclude so the worktree remains clean.
        common = Path(w.git(self.wt,"rev-parse","--git-common-dir").stdout.strip())
        with (common/"info"/"exclude").open("a") as out: out.write("\n__pycache__/\n")
        self.assertFalse(w._dirty(self.wt))
        with patch.object(ret,"_require_operator"), patch.object(ret,"_check_handles"):
            with self.assertRaisesRegex(w.WorkspaceError,"unexpected ignored content"):
                self.gc()
        self.assertTrue((self.wt/"__pycache__"/"unique.wav").exists())

    def test_integrated_but_later_committed_unintegrated_change_kept(self):
        (self.wt/"another.txt").write_text("never integrated")
        run("git","-C",str(self.wt),"add","another.txt")
        run("git","-C",str(self.wt),"commit","-qm","unique later")
        with patch.object(ret,"_require_operator"), patch.object(ret,"_check_handles"):
            with self.assertRaisesRegex(w.WorkspaceError,"registration, lock or HEAD mismatch"):
                self.gc()
        self.assertTrue((self.wt/"another.txt").exists())

    def test_remote_branch_advanced_preserved(self):
        temp=self.root/"other"
        run("git","clone","-q",str(self.origin),str(temp))
        run("git","-C",str(temp),"config","user.name","Other")
        run("git","-C",str(temp),"config","user.email","other@example.invalid")
        run("git","-C",str(temp),"checkout","-q","-B","new",self.job["last_checkpoint"])
        (temp/"new.txt").write_text("remote only")
        run("git","-C",str(temp),"add","new.txt")
        run("git","-C",str(temp),"commit","-qm","later work")
        run("git","-C",str(temp),"push","origin",f"HEAD:refs/heads/{self.job['branch']}")
        with patch.object(ret,"_require_operator"), patch.object(ret,"_check_handles"):
            with self.assertRaisesRegex(w.WorkspaceError,"remote checkpoint changed"):
                self.gc()
        self.assertTrue(self.wt.exists())

    def test_runtime_removal_error_relocks_and_preserves_state(self):
        old_git = w.git
        def fail_remove(repo,*args,**kwargs):
            if args[:2] == ("worktree","remove"):
                return subprocess.CompletedProcess(["git"], 1, "", "simulated")
            return old_git(repo,*args,**kwargs)
        with patch.object(ret,"_require_operator"), patch.object(ret,"_check_handles"),\
             patch.object(w,"git",side_effect=fail_remove):
            with self.assertRaisesRegex(w.WorkspaceError,"ambiguous postcondition"):
                self.gc()
        registered=run("git","-C",str(self.repo),"worktree","list","--porcelain").stdout
        self.assertIn("locked",registered)
        self.assertTrue(self.wt.exists())
        self.assertFalse(w.load_job(self.state,self.job["job_id"]).get("worktree_released_at"))

    def test_crash_after_remove_before_metadata_commit_is_reconciled(self):
        original=w.save_job
        tripped=[False]
        def fail_final(state,job):
            if job.get("worktree_released_at") and not tripped[0]:
                tripped[0]=True
                raise OSError("simulated power loss while committing receipt")
            return original(state,job)
        with patch.object(ret,"_require_operator"), patch.object(ret,"_check_handles"),\
             patch.object(w,"save_job",side_effect=fail_final):
            with self.assertRaisesRegex(w.WorkspaceError,"retirement inspection failed"):
                self.gc()
        self.assertFalse(self.wt.exists())
        interrupted=w.load_job(self.state,self.job["job_id"])
        self.assertIsNotNone(interrupted.get("retirement_intent"))
        self.assertIsNone(interrupted.get("worktree_released_at"))
        with patch.object(ret,"_require_operator"), patch.object(ret,"_check_handles"):
            result=self.gc()
        self.assertTrue(result[0].get("reconciled_after_interruption"))
        self.assertTrue(w.load_job(self.state,self.job["job_id"])["worktree_released_at"])
        self.assertTrue(self.receipt.exists())

    def test_crash_after_unlock_before_remove_restores_lock_and_rechecks(self):
        job=w.load_job(self.state,self.job["job_id"])
        job["retirement_intent"]={"job_id":job["job_id"],
            "checkpoint":job["last_remote_checkpoint"],
            "integration_commit":job["integration_commit"],"inspection":{}}
        w.save_job(self.state,job)
        w.git(self.repo,"worktree","unlock",str(self.wt))
        self.assertNotIn("locked",run("git","-C",str(self.repo),"worktree","list","--porcelain").stdout)
        with patch.object(ret,"_require_operator"), patch.object(ret,"_check_handles"):
            result=self.gc()
        self.assertTrue(result[0]["retired"])
        self.assertFalse(self.wt.exists())

    def test_new_workspace_defers_lfs_by_default(self):
        self.assertTrue(self.job.get("lfs_deferred"))
        with self.assertRaisesRegex(w.WorkspaceError,"explicitly hydrate"):
            w.ensure_job(argparse.Namespace(state_dir=str(self.state),repo=str(self.repo),
                resource="paper:one",remote="origin",target="main",
                job_id=self.job["job_id"],worktree_root=None,push_initial=True,
                hydrate_lfs=True))

    def test_lfs_pointer_only_checkout_does_not_need_cache_object(self):
        import hashlib
        payload=b"unmaterialised-media"
        digest=hashlib.sha256(payload).hexdigest()
        pointer=(f"version https://git-lfs.github.com/spec/v1\n"
                 f"oid sha256:{digest}\nsize {len(payload)}\n").encode()
        (self.wt/".gitattributes").write_text("media.bin filter=lfs -text\n")
        (self.wt/"media.bin").write_bytes(pointer)
        run("git","-C",str(self.wt),"add",".gitattributes","media.bin")
        run("git","-C",str(self.wt),"commit","-qm","synthetic pointer-only checkout")
        info=ret._check_checkout(w,self.repo,self.wt)
        self.assertEqual(info["lfs_pointer_only"],1)
        self.assertEqual(info["verified_lfs_bytes"],0)

    def test_lfs_with_unverified_cache_is_preserved(self):
        import hashlib
        payload=b"original-media-content-not-a-pointer"
        digest=hashlib.sha256(payload).hexdigest()
        pointer=(f"version https://git-lfs.github.com/spec/v1\n"
                 f"oid sha256:{digest}\nsize {len(payload)}\n").encode()
        (self.wt/".gitattributes").write_text("media.bin filter=lfs -text\n")
        (self.wt/"media.bin").write_bytes(pointer)
        run("git","-C",str(self.wt),"add",".gitattributes","media.bin")
        run("git","-C",str(self.wt),"commit","-qm","synthetic LFS fixture")
        # We only exercise the content scanner, NOT GC, because this new commit
        # is intentionally not in the historical integration record.
        old=ret._git_bytes
        def filtered_status(path,*args,**kwargs):
            if args and args[0] == "status":
                return b""  # test just the LFS path, not a clean-filter integration
            return old(path,*args,**kwargs)
        (self.wt/"media.bin").write_bytes(payload)
        with patch.object(ret,"_git_bytes",side_effect=filtered_status):
            with self.assertRaisesRegex(w.WorkspaceError,"LFS payload"):
                ret._check_checkout(w,self.repo,self.wt)
        common=Path(w.git(self.wt,"rev-parse","--git-common-dir").stdout.strip())
        location=common/"lfs"/"objects"/digest[:2]/digest[2:4]/digest
        location.parent.mkdir(parents=True,exist_ok=True)
        location.write_bytes(payload)
        with patch.object(ret,"_git_bytes",side_effect=filtered_status):
            ok=ret._check_checkout(w,self.repo,self.wt)
        self.assertEqual(ok["lfs_objects"],1)
        self.assertEqual(ok["verified_lfs_bytes"],len(payload))
        location.write_bytes(b"tampered"+payload)
        with patch.object(ret,"_git_bytes",side_effect=filtered_status):
            with self.assertRaisesRegex(w.WorkspaceError,"LFS payload"):
                ret._check_checkout(w,self.repo,self.wt)

    def test_unknown_filtered_content_is_preserved(self):
        (self.wt/".gitattributes").write_text("file.txt filter=custom-tool\n")
        run("git","-C",str(self.wt),"add",".gitattributes")
        run("git","-C",str(self.wt),"commit","-qm","test unknown filter")
        with self.assertRaisesRegex(w.WorkspaceError,"unknown external filter"):
            ret._check_checkout(w,self.repo,self.wt)

    def test_changed_integration_policy_receipt_is_preserved(self):
        receipt=json.loads(self.receipt.read_text())
        receipt["policy_sha256"]="0"*64
        self.receipt.write_text(json.dumps(receipt))
        with patch.object(ret,"_require_operator"), patch.object(ret,"_check_handles"):
            with self.assertRaisesRegex(w.WorkspaceError,"policy/tree mismatch"):
                self.gc()
        self.assertTrue(self.wt.exists())

    def test_repository_wide_gc_disallowed(self):
        with patch.object(ret,"_require_operator"), patch.object(ret,"_check_handles"):
            with self.assertRaisesRegex(w.WorkspaceError,"exact --repo and --job"):
                self.gc(job=None)
        self.assertTrue(self.wt.exists())

    def test_still_within_retention_disallowed(self):
        data=json.loads(w.job_path(self.state,self.job["job_id"]).read_text())
        data["integrated_at"]=w.now_iso()
        w.job_path(self.state,self.job["job_id"]).write_text(json.dumps(data))
        with patch.object(ret,"_require_operator"), patch.object(ret,"_check_handles"):
            with self.assertRaisesRegex(w.WorkspaceError,"retention"):
                self.gc()
        self.assertTrue(self.wt.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
