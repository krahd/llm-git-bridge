import os, subprocess, tempfile, unittest, json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
class AtomicMigratorTests(unittest.TestCase):
    def make_fixture(self, td):
        home=Path(td)/'home'; (home/'Library/LaunchAgents').mkdir(parents=True); (home/'Library/Logs').mkdir(parents=True)
        suffix='testcandidate'; label='net.laurenzo.local-executor-bridge-v6-'+suffix
        (home/'Library/LaunchAgents/net.laurenzo.local-executor-bridge-v6-staging.plist').write_text('old')
        (home/f'Library/LaunchAgents/{label}.plist').write_text('new')
        install=home/f'.local/share/local-executor-bridge-v6-{suffix}'; install.mkdir(parents=True)
        (install/'bridge.py').write_text('import sys\nraise SystemExit(0) if len(sys.argv)>1 and sys.argv[1]=="doctor" else None\n')
        (install/'approval_helper.py').write_text('Allow once Reject menu only')
        (install/'trusted_operations.py').write_text('registered policy')
        (install/'bridge_mailbox_inspector.py').write_text('inspector')
        app=install/'Local Executor Approval.app'; app.mkdir()
        cfgdir=home/f'.config/local-executor-bridge-v6-{suffix}'; cfgdir.mkdir(parents=True)
        (cfgdir/'config.json').write_text(json.dumps({'operator_approval_app':str(app),'operator_confirmation_mode':'auto','bridge_instance_id':'iid','remote':'r:','drive_root_folder_id':'root','state_dir':str(home/'state')}))
        calls=Path(td)/'calls'; fake=Path(td)/'launchctl'; fake.write_text('#!/bin/bash\necho "$@" >> "'+str(calls)+'"\nexit 0\n'); fake.chmod(0o755)
        return home,suffix,fake,calls
    def run_migrator(self, home, suffix, fake, extra=None):
        env=os.environ.copy(); env.update({'MIGRATOR_HOME':str(home),'MIGRATOR_UID':'501','MIGRATOR_LAUNCHCTL':str(fake),'MIGRATOR_SKIP_STAGE':'1','MIGRATOR_SKIP_HEALTH':'1'})
        if extra: env.update(extra)
        return subprocess.run(['/bin/bash',str(ROOT/'migrate_v6_staging_atomic.sh'),suffix],env=env,text=True,capture_output=True)
    def test_dry_run_does_not_switch_services(self):
        with tempfile.TemporaryDirectory() as td:
            home,suffix,fake,calls=self.make_fixture(td); r=self.run_migrator(home,suffix,fake,{'MIGRATOR_DRY_RUN':'1'}); self.assertEqual(r.returncode,0,r.stderr); text=calls.read_text(); self.assertIn('print gui/501/net.laurenzo.local-executor-bridge-v6-staging',text); self.assertNotIn('bootout',text); self.assertNotIn('bootstrap',text)
    def test_failure_after_candidate_start_rolls_back_old_v6_only(self):
        with tempfile.TemporaryDirectory() as td:
            home,suffix,fake,calls=self.make_fixture(td); r=self.run_migrator(home,suffix,fake,{'MIGRATOR_FAIL_AFTER_START':'1'}); self.assertNotEqual(r.returncode,0); text=calls.read_text(); self.assertIn('bootout gui/501/net.laurenzo.local-executor-bridge-v6-staging',text); self.assertIn('bootstrap gui/501 '+str(home/'Library/LaunchAgents/net.laurenzo.local-executor-bridge-v6-staging.plist'),text); self.assertNotIn('io.llm-git-bridge.daemon',text); self.assertNotIn('com.tom.chatgpt-shell-bridge',text)
    def test_repeat_migration_retires_and_restores_active_candidate(self):
        with tempfile.TemporaryDirectory() as td:
            home,suffix,fake,calls=self.make_fixture(td)
            prev='net.laurenzo.local-executor-bridge-v6-candidate-previous'
            prev_plist=home/f'Library/LaunchAgents/{prev}.plist'; prev_plist.write_text('previous')
            fake.write_text('#!/bin/bash\necho "$@" >> "'+str(calls)+'"\nif [ "$1" = print ]; then case "$2" in *candidate-previous) exit 0;; *) exit 1;; esac; fi\nexit 0\n'); fake.chmod(0o755)
            r=self.run_migrator(home,suffix,fake,{'MIGRATOR_FAIL_AFTER_START':'1'})
            self.assertNotEqual(r.returncode,0)
            text=calls.read_text()
            self.assertIn('bootout gui/501/'+prev,text)
            self.assertIn('bootstrap gui/501 '+str(prev_plist),text)
            self.assertIn('kickstart -k gui/501/'+prev,text)
            self.assertNotIn('bootout gui/501/net.laurenzo.local-executor-bridge-v6-staging\n',text)

    def test_source_never_targets_v5(self):
        s=(ROOT/'migrate_v6_staging_atomic.sh').read_text(); self.assertNotIn('io.llm-git-bridge.daemon',s); self.assertNotIn('com.tom.chatgpt-shell-bridge',s); self.assertNotIn('chatgpt-shell-bridge/config',s)
    def test_app_builder_uses_single_atomic_migrator(self):
        s=(ROOT/'build_v6_staging_migrator_app.sh').read_text(); self.assertIn('migrate_v6_staging_atomic.sh',s); self.assertNotIn('osascript',s); self.assertNotIn('LocalAuthentication',s)
if __name__=='__main__': unittest.main()
