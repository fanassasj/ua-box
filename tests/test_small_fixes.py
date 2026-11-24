"""Non-Docker fixes. Git, crontab, network and large disk writes are isolated fakes."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

import test_regressions as base
from test_doctor_notifications import health_module


class DiskProbeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        self.env = dict(os.environ, UA_IO_TEST_DIR=str(self.work), UA_IO_TEST_MB='1', MODE='ok', READY=str(self.work / 'ready'))
        self.source = base.shell_function('ua_disk_io_test') + r'''
df() { printf 'Filesystem Blocks Used Available Use%% Mounted\nfake 1000000 1 %s 1%% /\n' "${FREE_KB:-1000000}"; }
dd() {
  touch "$READY"
  case "$MODE" in
    fail) printf partial > data; return 1 ;;
    short) printf partial > data; return 0 ;;
    wait) sleep 10 ;;
    ok)
      python3 -c 'from pathlib import Path; Path("data").write_bytes(bytes(1024*1024))'
      printf '1048576 bytes copied, 0.01 s, 100 MB/s\n' >&2 ;;
  esac
}
ua_disk_io_test
'''

    def run_probe(self):
        return subprocess.run(['/bin/bash', '-c', self.source], env=self.env, capture_output=True, text=True, timeout=10)

    def test_fixed_filename_user_data_is_preserved_and_only_owned_directory_cleaned(self):
        user_file = self.work / 'ua_io_speed_test_file'
        user_file.write_text('do not overwrite')
        unrelated = self.work / '.ua-io-user'
        unrelated.mkdir(); (unrelated / 'data').write_text('keep')
        result = self.run_probe()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('100 MB/s', result.stdout)
        self.assertEqual(user_file.read_text(), 'do not overwrite')
        self.assertEqual((unrelated / 'data').read_text(), 'keep')
        self.assertEqual(set(p.name for p in self.work.iterdir()), {'ua_io_speed_test_file', '.ua-io-user', 'ready'})

    def test_dd_failure_and_short_write_do_not_report_speed(self):
        for mode in ['fail', 'short']:
            with self.subTest(mode=mode):
                self.env['MODE'] = mode
                result = self.run_probe()
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('磁盘写入速度:', result.stdout)
                self.assertFalse(list(self.work.glob('.ua-io.*')))

    def test_low_disk_space_refuses_before_any_test_write(self):
        self.env['FREE_KB'] = '1024'
        result = self.run_probe()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.work / 'ready').exists())
        self.assertFalse(list(self.work.glob('.ua-io.*')))

    def test_invalid_size_refuses_before_test(self):
        for value in ['0', '4097', '-1', '999999999999999', '1;echo unsafe']:
            self.env['UA_IO_TEST_MB'] = value
            self.assertNotEqual(self.run_probe().returncode, 0)
        self.assertFalse((self.work / 'ready').exists())

    def test_signal_cleanup_preserves_user_files(self):
        for sig in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(sig=sig):
                ready = self.work / 'ready'
                ready.unlink(missing_ok=True)
                keep = self.work / 'keep'; keep.write_text('original')
                process = subprocess.Popen(['/bin/bash', '-c', self.source], env=dict(self.env, MODE='wait'),
                                           start_new_session=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                try:
                    deadline = time.monotonic() + 5
                    while not ready.exists() and time.monotonic() < deadline:
                        time.sleep(0.01)
                    self.assertTrue(ready.exists())
                    os.killpg(process.pid, sig)
                    process.communicate(timeout=5)
                    self.assertNotEqual(process.returncode, 0)
                    self.assertFalse(list(self.work.glob('.ua-io.*')))
                    self.assertEqual(keep.read_text(), 'original')
                finally:
                    if process.poll() is None:
                        os.killpg(process.pid, signal.SIGKILL); process.communicate(timeout=5)


class CronPlanTests(unittest.TestCase):
    def setUp(self):
        self.mod = health_module()
        self.exe = '/usr/local/bin/ua'
        self.script = '/root/ua-box/ua-box'
        self.other = '# personal jobs\nMAILTO=private@example.invalid\n*/5 * * * * /opt/another-app status --notify\n0 1 * * * /opt/keep-job\n'

    def plan(self, text, action):
        return self.mod.notify_cron_plan(text, self.exe, self.script, action)

    def test_enable_disable_preserves_other_program_notify_jobs_and_comments(self):
        enabled = self.plan(self.other, 'enable')
        self.assertTrue(enabled.startswith(self.other))
        self.assertIn('# ua-box:status-notify', enabled)
        self.assertEqual(self.plan(enabled, 'disable'), self.other)

    def test_repeated_enable_has_exactly_one_managed_job(self):
        once = self.plan(self.other, 'enable')
        self.assertEqual(self.plan(once, 'enable'), once)
        self.assertEqual(once.count(self.mod.CRON_MARKER), 1)

    def test_repeated_disable_is_idempotent(self):
        self.assertEqual(self.plan(self.other, 'disable'), self.other)

    def test_only_exact_generated_legacy_job_is_migrated(self):
        legacy = '*/30 * * * * /usr/local/bin/ua status --notify >/dev/null 2>&1\n'
        custom = '*/5 * * * * /usr/local/bin/ua status --notify >/dev/null 2>&1\n'
        result = self.plan(self.other + legacy + custom, 'enable')
        self.assertIn(custom, result)
        self.assertNotIn(legacy, result)
        self.assertEqual(result.count(self.mod.CRON_MARKER), 1)

    def test_executable_spaces_quotes_percent_and_shell_characters_are_quoted(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            executable = work / "ua with spaces%'$(touch X)"
            executable.write_text('#!/bin/bash\nprintf invoked > "$CALLED"\n')
            executable.chmod(0o700)
            result = self.mod.notify_cron_plan('', str(executable), self.script, 'enable')
            self.assertIn('\\%', result)
            self.assertIn("'\"'\"'", result)
            self.assertEqual(self.mod.notify_cron_plan(result, str(executable), self.script, 'disable'), '')
            command = result.strip().split(None, 5)[5].rsplit(' ' + self.mod.CRON_MARKER, 1)[0].replace('\\%', '%')
            called = work / 'called'
            process = subprocess.run(['/bin/bash', '-c', command], cwd=work, env=dict(os.environ, CALLED=str(called)), timeout=5)
            self.assertEqual(process.returncode, 0)
            self.assertEqual(called.read_text(), 'invoked')
            self.assertFalse((work / 'X').exists())

    def test_unrelated_ua_alias_is_not_assumed_to_belong_to_this_installation(self):
        line = '*/30 * * * * /usr/local/bin/ua status --notify >/dev/null 2>&1\n'
        result = self.mod.notify_cron_plan(line, '/custom/own-ua', '/custom/ua-box', 'disable')
        self.assertEqual(result, line)

    def test_control_character_in_executable_is_rejected(self):
        with self.assertRaises(self.mod.HealthError):
            self.mod.notify_cron_plan('', '/tmp/ua\n* * * * * malicious', self.script, 'enable')


class CronApplyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        for name in ['tmp', 'backups']:
            (self.work / name).mkdir(mode=0o700)
        executable = self.work / 'ua-box'
        executable.write_text('#!/bin/bash\nexit 0\n'); executable.chmod(0o700)
        self.table = self.work / 'crontab'
        self.original = '# keep\n*/5 * * * * /opt/other status --notify\n'
        self.table.write_text(self.original)
        self.env = dict(os.environ, UA_BOX_HOME=str(self.work), SCRIPT_REAL_PATH=str(self.work / 'ua-box'),
                        UA_BOX_TMP_DIR=str(self.work / 'tmp'), UA_BOX_BACKUP_DIR=str(self.work / 'backups'),
                        UA_NOTIFY_STATE_DIR=str(self.work / 'state'), TABLE=str(self.table), CALLS=str(self.work / 'calls'), MODE='ok')
        self.code = '\n'.join(base.shell_function(name) for name in ['ua_notify_cron_update', 'ua_notify_cron_status', 'ua_crontab_read', 'ua_health_operation', 'ua_health_program']) + r'''
need_root() { return 0; }; has_cmd() { command -v "$1" >/dev/null; }; log_action() { :; }
crontab() {
  if [ "$1" = -l ]; then
    case "$MODE" in
      read-fail) echo 'permission denied' >&2; return 1 ;;
      ambiguous) return 1 ;;
      absent) if [ ! -f "$TABLE" ]; then echo 'no crontab for fakeuser' >&2; return 1; fi ;;
      race)
        if [ -f "$TABLE.seen" ]; then printf '0 2 * * * /opt/new-job\n' >> "$TABLE"; fi
        touch "$TABLE.seen" ;;
    esac
    cat "$TABLE"
  else
    echo install >> "$CALLS"
    [ "$MODE" != write-fail ] || return 1
    if [ "$MODE" = wrong-result ]; then printf 'unconfirmed result\n' > "$TABLE"; else cp "$1" "$TABLE"; fi
  fi
}
'''

    def apply(self, action='enable'):
        return subprocess.run(['/bin/bash', '-c', self.code + '\nua_notify_cron_update ' + action],
                              env=self.env, capture_output=True, text=True, timeout=10)

    def test_apply_preserves_unrelated_tasks_and_retains_private_original(self):
        result = self.apply()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(self.table.read_text().startswith(self.original))
        backups = list((self.work / 'backups').glob('*.bak'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(), self.original)
        self.assertEqual(backups[0].stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.apply().returncode, 0)
        self.assertEqual((self.work / 'calls').read_text().splitlines(), ['install'])
        self.assertEqual(self.apply('disable').returncode, 0)
        self.assertEqual(self.table.read_text(), self.original)

    def test_read_error_or_ambiguous_empty_result_never_installs(self):
        for mode in ['read-fail', 'ambiguous']:
            self.env['MODE'] = mode
            result = self.apply()
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(self.table.read_text(), self.original)
            self.assertFalse((self.work / 'calls').exists())

    def test_missing_crontab_can_be_initialized(self):
        self.table.unlink(); self.env['MODE'] = 'absent'
        self.assertEqual(self.apply().returncode, 0)
        self.assertIn('# ua-box:status-notify', self.table.read_text())

    def test_write_failure_is_not_success(self):
        self.env['MODE'] = 'write-fail'
        result = self.apply()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.table.read_text(), self.original)
        self.assertNotIn('enable 已核验', result.stdout)
        self.assertTrue(list((self.work / 'backups').glob('*.bak')))

    def test_postwrite_mismatch_is_reported(self):
        self.env['MODE'] = 'wrong-result'
        result = self.apply()
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('enable 已核验', result.stdout)

    def test_concurrent_external_edit_is_not_overwritten(self):
        self.env['MODE'] = 'race'
        result = self.apply()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('/opt/new-job', self.table.read_text())
        self.assertFalse((self.work / 'calls').exists())

    def test_failed_crontab_backup_blocks_install(self):
        self.code += '\ncp() { return 1; }\n'
        result = self.apply()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.table.read_text(), self.original)
        self.assertFalse((self.work / 'calls').exists())
        self.assertFalse(list((self.work / 'backups').glob('*.bak')))

    def test_nonexecutable_script_cannot_be_reported_as_enabled(self):
        (self.work / 'ua-box').chmod(0o600)
        result = self.apply()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.table.read_text(), self.original)
        self.assertFalse((self.work / 'calls').exists())

    def test_status_does_not_mistake_other_program_for_ua_task(self):
        result = subprocess.run(['/bin/bash', '-c', self.code + '\nua_notify_cron_status'], env=self.env,
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), '未开启')
        self.assertFalse((self.work / 'calls').exists())


class SelfUpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        for name in ['tmp', 'backups', 'bin', '.git']:
            (self.work / name).mkdir(mode=0o700)
        self.src = self.work / 'ua-box'
        self.src.write_text('#!/bin/bash\nUA_BOX_VERSION="0.6.4"\ncreate_local_links() { :; }\n')
        self.original = self.src.read_bytes()
        self.candidate = self.work / 'candidate'
        self.candidate.write_text('#!/bin/bash\nUA_BOX_VERSION="0.6.5"\ncreate_local_links() { :; }\nprintf updated > "$EXECUTED"\n')
        self.head = self.work / 'head'; self.head.write_text('a' * 40)
        fake = self.work / 'bin/git'
        fake.write_text('#!' + sys.executable + '\n' + r'''
import json, os, pathlib, shutil, sys
args=sys.argv[3:]
work=pathlib.Path(os.environ['WORK'])
with (work/'git-calls').open('a') as out: out.write(json.dumps(args)+'\n')
cmd=args[0]; mode=os.environ.get('MODE','ok')
if cmd=='symbolic-ref':
    if mode=='detached': sys.exit(1)
    print('feat/test')
elif cmd=='rev-parse':
    if '--abbrev-ref' in args:
        if mode=='missing-upstream': sys.exit(1)
        print('origin/feat/test')
    elif '--verify' in args: print((work/'head').read_text() if mode=='up-to-date' else 'b'*40)
    else: print((work/'head').read_text())
elif cmd=='status':
    if mode=='dirty' or mode=='race' and (work/'fetched').exists(): print(' M config')
elif cmd=='fetch':
    if mode=='fetch-fail': sys.exit(1)
    (work/'fetched').touch()
elif cmd=='merge-base': sys.exit(1 if mode=='diverged' else 0)
elif cmd=='show': sys.stdout.buffer.write((work/'candidate').read_bytes())
elif cmd=='merge':
    if mode=='merge-fail': sys.exit(1)
    shutil.copyfile(work/'candidate',work/'ua-box'); (work/'head').write_text('b'*40)
else: raise SystemExit('unexpected Git operation')
''')
        fake.chmod(0o700)
        self.env = dict(os.environ, PATH=str(self.work / 'bin') + ':' + os.environ['PATH'], WORK=str(self.work),
                        UA_BOX_HOME=str(self.work), UA_BOX_TMP_DIR=str(self.work / 'tmp'), UA_BOX_BACKUP_DIR=str(self.work / 'backups'),
                        EXECUTED=str(self.work / 'executed'), AUDIT=str(self.work / 'audit'),
                        UA_BOX_UPDATE_URL='https://user:TEST-PASSWORD@example.invalid/update?token=TEST-TOKEN', MODE='ok', LINK_RC='0')
        self.source = '\n'.join(base.shell_function(name) for name in ['update_self', 'ua_validate_update', 'backup_self']) + r'''
has_cmd() { command -v "$1" >/dev/null; }
prune_old_backups() { :; }
log_action() { printf '%s\n' "$*" >> "$AUDIT"; }
create_local_links() { return "$LINK_RC"; }
ua_cleanup_tmp() { :; }
download_to() { [ "$MODE" != download-fail ] || return 1; cp "$WORK/candidate" "$2"; }
update_self
'''

    def run_update(self, stdin='y\n', source=None):
        return subprocess.run(['/bin/bash', '-c', self.source if source is None else source, str(self.src)],
                              input=stdin, env=self.env, capture_output=True, text=True, timeout=10)

    def test_git_fetch_failure_returns_failure_without_changing_script(self):
        self.env['MODE'] = 'fetch-fail'
        result = self.run_update()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.src.read_bytes(), self.original)
        self.assertFalse((self.work / 'executed').exists())

    def test_missing_branch_upstream_or_eof_never_fetches(self):
        for mode, stdin in [('missing-upstream', 'y\n'), ('detached', 'y\n'), ('ok', '')]:
            with self.subTest(mode=mode):
                self.env['MODE'] = mode
                result = self.run_update(stdin)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.work / 'fetched').exists())
                self.assertEqual(self.src.read_bytes(), self.original)

    def test_up_to_date_git_branch_does_not_reexecute_or_create_backup(self):
        self.env['MODE'] = 'up-to-date'
        result = self.run_update()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('无需更新', result.stdout)
        self.assertFalse((self.work / 'executed').exists())
        self.assertFalse(list((self.work / 'backups').iterdir()))

    def test_git_only_fast_forwards_after_validation_and_preserves_backup(self):
        result = self.run_update()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.work / 'executed').read_text(), 'updated')
        self.assertEqual(next((self.work / 'backups').glob('*.bak')).read_bytes(), self.original)
        calls = (self.work / 'git-calls').read_text()
        self.assertIn('"merge", "--ff-only"', calls)
        self.assertNotIn('"reset"', calls)
        self.assertNotIn('"commit"', calls)
        self.assertIn('feat/test', result.stdout)
        self.assertIn('origin/feat/test', result.stdout)

    def test_dirty_or_changed_workspace_is_never_overwritten(self):
        for mode in ['dirty', 'race', 'diverged', 'merge-fail']:
            with self.subTest(mode=mode):
                self.env['MODE'] = mode
                result = self.run_update()
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.src.read_bytes(), self.original)
                self.assertFalse((self.work / 'executed').exists())

    def test_bad_git_candidate_is_refused_before_merge(self):
        self.candidate.write_text('echo not-ua-box\n')
        result = self.run_update()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.src.read_bytes(), self.original)
        self.assertNotIn('"merge",', (self.work / 'git-calls').read_text())

    def test_url_success_redacts_password_and_token(self):
        result = self.run_update('n\n1\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.work / 'executed').read_text(), 'updated')
        audit = (self.work / 'audit').read_text()
        for secret in ['TEST-PASSWORD', 'TEST-TOKEN', 'user:']:
            self.assertNotIn(secret, result.stdout + result.stderr + audit)

    def test_bad_download_and_invalid_candidate_leave_original(self):
        self.env['MODE'] = 'download-fail'
        self.assertNotEqual(self.run_update('n\n1\n').returncode, 0)
        self.env['MODE'] = 'ok'
        for candidate in ['echo unrelated\n', 'UA_BOX_VERSION="0.6.5"\ncreate_local_links() {\n']:
            self.candidate.write_text(candidate)
            result = self.run_update('n\n1\n')
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(self.src.read_bytes(), self.original)

    def test_invalid_embedded_python_is_refused_without_execution(self):
        self.candidate.write_text("#!/bin/bash\nUA_BOX_VERSION=\"0.6.5\"\ncreate_local_links() { :; }\nf() {\ncat <<'PY_UA_HEALTH'\ninvalid python @\nPY_UA_HEALTH\n}\n")
        result = self.run_update('n\n1\n')
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.src.read_bytes(), self.original)

    def test_backup_failure_blocks_url_replacement(self):
        code = self.source.rsplit('update_self\n', 1)[0] + '\nbackup_self() { return 1; }\nupdate_self\n'
        result = self.run_update('n\n1\n', code)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.src.read_bytes(), self.original)

    def test_link_failure_is_explicit_partial_failure_without_reexecution(self):
        self.env['LINK_RC'] = '1'
        result = self.run_update('n\n1\n')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('脚本已更新', result.stderr)
        self.assertFalse((self.work / 'executed').exists())

    def test_actual_backup_copy_failure_keeps_previous_backups(self):
        old = self.work / 'backups/previous.bak'; old.write_text('keep')
        code = self.source.rsplit('update_self\n', 1)[0] + '\ncp() { return 1; }\nbackup_self\n'
        result = self.run_update('', code)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(old.read_text(), 'keep')
        self.assertEqual(list((self.work / 'backups').iterdir()), [old])
