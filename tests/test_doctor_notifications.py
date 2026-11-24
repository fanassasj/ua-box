"""Doctor and acknowledged alerts. All service/network calls are simulated."""
import copy
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import tempfile
import time
import types
import unittest
from unittest import mock

import test_regressions as base


def health_module():
    code = base.SCRIPT.split("<<'PY_UA_HEALTH'\n", 1)[1].split('\nPY_UA_HEALTH', 1)[0]
    module = types.ModuleType('ua_health_tests')
    exec(compile(code, 'ua_health_tests', 'exec'), module.__dict__)
    return module


class DoctorTests(unittest.TestCase):
    def setUp(self):
        self.mod = health_module()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        self.config = self.work / 'config'
        self.config.write_text("UA_NOTIFY_COOLDOWN_SECONDS='3600'\n")
        self.config.chmod(0o600)
        self.commands = []
        def query(args):
            self.commands.append(args)
            if args[0] == 'bash':
                return 0, '5' if '-c' in args else ''
            if args[:3] == ['docker', 'context', 'inspect']:
                return 0, '[{"Endpoints":{"docker":{"Host":"unix:///fake/docker.sock"}}}]'
            if '--help' in args:
                return 0, '--format string'
            return 0, ''
        self.mod.query = query
        lookup = mock.patch.object(self.mod.shutil, 'which', side_effect=lambda name: '/fake/' + name)
        lookup.start()
        self.addCleanup(lookup.stop)
        disk = mock.patch.object(self.mod.shutil, 'disk_usage', return_value=types.SimpleNamespace(free=1024**3))
        disk.start()
        self.addCleanup(disk.stop)
        environment = mock.patch.dict(os.environ, {'DOCKER_HOST': '', 'DOCKER_CONTEXT': '', 'BASH_ENV': ''})
        environment.start()
        self.addCleanup(environment.stop)

    def diagnose(self):
        return self.mod.doctor(str(self.work), str(self.config), str(self.work / 'logs'),
                               str(self.work / 'backups'), str(self.work / 'state'))

    def test_all_checks_only_use_read_queries_and_create_no_directories(self):
        before = {p.name: p.read_bytes() for p in self.work.iterdir()}
        report = self.diagnose()
        self.assertTrue(report['read_only'])
        self.assertEqual(report['exit_code'], 0)
        self.assertTrue(report['capabilities']['local_docker_socket'])
        self.assertTrue(report['capabilities']['compose_config_format'])
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.work.iterdir()})
        allowed = {('docker', 'version'), ('docker', 'context'), ('docker', 'compose')}
        for args in self.commands:
            if args[0] == 'docker':
                self.assertIn(tuple(args[:2]), allowed)
                self.assertNotIn('up', args)
                self.assertNotIn('restart', args)

    def test_config_commands_are_never_evaluated_and_secrets_are_redacted(self):
        marker = self.work / 'EXECUTED'
        self.config.write_text('UA_NOTIFY_TG_BOT_TOKEN="VERY-PRIVATE-TOKEN"\n' +
                               'VALUE="$(touch ' + str(marker) + ')"\n')
        report = self.diagnose()
        self.assertFalse(marker.exists())
        self.assertNotIn('VERY-PRIVATE-TOKEN', json.dumps(report))
        self.assertEqual(next(c for c in report['checks'] if c['id'] == 'config.static')['status'], 'WARN')

    def test_config_permission_error_is_reported_without_chmod(self):
        self.config.chmod(0o666)
        report = self.diagnose()
        self.assertEqual(report['exit_code'], 2)
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o666)

    def test_config_owner_mismatch_is_reported_without_chown(self):
        owner = self.config.stat().st_uid
        with mock.patch.object(self.mod.os, 'geteuid', return_value=owner + 1000):
            report = self.diagnose()
        self.assertEqual(next(c for c in report['checks'] if c['id'] == 'config.owner')['status'], 'WARN')
        self.assertEqual(self.config.stat().st_uid, owner)

    def test_missing_config_is_not_created(self):
        self.config.unlink()
        report = self.diagnose()
        self.assertEqual(report['exit_code'], 1)
        self.assertFalse(self.config.exists())

    def test_docker_unreachable_is_not_healthy_and_does_not_start_service(self):
        real = self.mod.query
        self.mod.query = lambda args: (125, 'PRIVATE-CONNECTION-STRING') if args[:2] == ['docker', 'version'] else real(args)
        report = self.diagnose()
        self.assertFalse(report['capabilities']['docker_readable'])
        self.assertFalse(report['capabilities']['local_docker_socket'])
        self.assertNotIn('PRIVATE-CONNECTION', json.dumps(report))
        self.assertEqual(report['exit_code'], 1)

    def test_remote_docker_context_does_not_claim_local_recovery(self):
        with mock.patch.dict(os.environ, {'DOCKER_HOST': 'tcp://user:secret@remote:2376'}):
            report = self.diagnose()
        self.assertFalse(report['capabilities']['local_docker_socket'])
        self.assertNotIn('user:secret', json.dumps(report))

    def test_optional_tools_missing_do_not_trigger_install(self):
        required = {'bash', 'readlink', 'awk', 'sed', 'grep', 'tar', 'mktemp', 'stat', 'curl', 'flock'}
        self.mod.shutil.which = lambda name: '/fake/' + name if name in required else None
        report = self.diagnose()
        self.assertFalse(report['capabilities']['docker_readable'])
        self.assertFalse(report['capabilities']['rclone_mount_prerequisites'])
        self.assertFalse(any('install' in args for args in self.commands))

    def test_bad_cooldown_is_detected_without_changing_config(self):
        self.config.write_text('UA_NOTIFY_COOLDOWN_SECONDS=999999\n')
        report = self.diagnose()
        self.assertEqual(report['exit_code'], 2)
        self.assertEqual(self.config.read_text(), 'UA_NOTIFY_COOLDOWN_SECONDS=999999\n')

    def test_query_clears_shell_startup_and_limits_time(self):
        module = health_module()
        with mock.patch.object(module.subprocess, 'run', return_value=types.SimpleNamespace(returncode=0, stdout='5')) as run:
            module.query(['bash', '-n', str(self.config)])
        self.assertEqual(run.call_args.kwargs['env']['BASH_ENV'], '')
        self.assertEqual(run.call_args.kwargs['env']['ENV'], '')
        self.assertEqual(run.call_args.kwargs['timeout'], 8)
        self.assertNotIn('shell', run.call_args.kwargs)

    def test_full_doctor_cli_is_read_only_even_with_executable_config(self):
        # Stub Docker before starting the complete script; never inspect the real daemon.
        commands = self.work / 'bin'
        commands.mkdir()
        for name in ['python3', 'bash', 'cat', 'readlink', 'dirname', 'awk', 'sed', 'grep', 'tar', 'stat', 'flock', 'curl']:
            executable = shutil.which(name)
            if executable and Path(executable).exists():
                (commands / name).symlink_to(executable)
        # setUp's which mock deliberately returns fake paths, so use fixed real tools here.
        for name, target in [('python3', os.path.realpath(os.sys.executable)), ('bash', '/bin/bash'), ('cat', '/bin/cat'),
                             ('readlink', '/usr/bin/readlink'), ('dirname', '/usr/bin/dirname')]:
            link = commands / name
            if not link.exists():
                link.symlink_to(target)
        marker = self.work / 'CONFIG-WAS-EXECUTED'
        self.config.write_text('touch ' + str(marker) + '\nUA_NOTIFY_TG_BOT_TOKEN="SUPER-SECRET"\n')
        docker = commands / 'docker'
        docker.write_text('#!/bin/bash\ncase "$*" in\nversion*) echo 27.0.0;;\n"context inspect") echo \'[{"Endpoints":{"docker":{"Host":"unix:///fake.sock"}}}]\';;\n"compose version --short") echo 2.0.0;;\n"compose config --help") echo --format;;\n*) exit 99;;\nesac\n')
        docker.chmod(0o700)
        mktemp = commands / 'mktemp'
        mktemp.write_text('#!/bin/bash\nexit 99\n')
        mktemp.chmod(0o700)
        env = dict(os.environ, PATH=str(commands), UA_BOX_CONFIG=str(self.config), UA_BOX_TMP_DIR=str(self.work / 'NO-TMP'),
                   UA_BOX_LOG_DIR=str(self.work / 'NO-LOGS'), UA_BOX_BACKUP_DIR=str(self.work / 'NO-BACKUPS'))
        result = subprocess.run(['/bin/bash', str(base.ROOT / 'ua-box'), 'doctor', '--json'], env=env,
                                capture_output=True, text=True, timeout=20)
        report = json.loads(result.stdout)
        self.assertTrue(report['read_only'])
        self.assertIn(result.returncode, (1, 2))
        self.assertFalse(marker.exists())
        for path in ['NO-TMP', 'NO-LOGS', 'NO-BACKUPS']:
            self.assertFalse((self.work / path).exists())
        self.assertNotIn('SUPER-SECRET', result.stdout + result.stderr)

    def test_missing_python_cli_reports_json_without_installing(self):
        commands = self.work / 'empty-bin'
        commands.mkdir()
        for name, target in [('readlink', '/usr/bin/readlink'), ('dirname', '/usr/bin/dirname')]:
            (commands / name).symlink_to(target)
        result = subprocess.run(['/bin/bash', str(base.ROOT / 'ua-box'), 'doctor', '--json'],
                                env=dict(os.environ, PATH=str(commands), UA_BOX_CONFIG=str(self.work / 'absent')),
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(json.loads(result.stdout)['checks'][0]['id'], 'python')
        self.assertFalse((self.work / 'absent').exists())


class NotificationStateTests(unittest.TestCase):
    def setUp(self):
        self.mod = health_module()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        self.directory = self.work / 'state'
        self.mod.prepare_state(self.directory)
        self.events = self.work / 'events'
        self.plan_path = self.work / 'plan'
        self.environment = {'UA_HEALTH_TG_TOKEN': 'VERY-SECRET-TOKEN', 'UA_HEALTH_TG_CHAT': 'private-chat'}

    def observed(self, issues=(), checked=('host.cpu',), unknown=(), ignored=()):
        rows = [{'type': 'checked', 'scope': value} for value in checked]
        rows += [{'type': 'unknown', 'scope': value} for value in unknown]
        rows += [{'type': 'ignored', 'key': key} for key in ignored]
        rows += [{'type': 'issue', 'key': key, **issue} for key, issue in issues]
        self.events.write_text(''.join(json.dumps(row) + '\n' for row in rows))

    def issue(self, key='host:cpu', severity='warning', message='CPU 90%', scope='host.cpu'):
        return key, {'scope': scope, 'severity': severity, 'message': message}

    def plan(self, now=1000, cooldown=3600):
        return self.mod.plan_delivery(self.directory, self.events, self.plan_path, cooldown, self.environment, now)

    def ack(self, kind='telegram', now=1000):
        self.mod.commit_delivery(self.directory, self.plan_path, kind, now)

    def active(self, kind='telegram'):
        state = self.mod.load_state(self.directory)
        return next(channel['active'] for channel in state['channels'].values() if channel['kind'] == kind)

    def test_first_fault_is_immediate_and_state_is_private_and_redacted(self):
        self.observed([self.issue()])
        plan = self.plan()
        self.assertIn('新故障', plan['channels']['telegram']['body'])
        self.assertFalse((self.directory / 'state.json').exists())
        self.ack()
        state = self.directory / 'state.json'
        self.assertEqual(state.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.directory.stat().st_mode & 0o777, 0o700)
        self.assertNotIn('VERY-SECRET-TOKEN', state.read_text())
        self.assertNotIn('private-chat', state.read_text())

    def test_repeated_fault_and_changing_metrics_are_deduplicated(self):
        self.observed([self.issue()]); self.plan(); self.ack()
        self.observed([self.issue(message='CPU 94%')])
        self.assertEqual(self.plan(now=1200)['channels'], {})
        self.assertEqual(self.active()['host:cpu']['last_sent'], 1000)

    def test_cooldown_expiry_reminds_then_resets_timestamp_on_ack(self):
        self.observed([self.issue()]); self.plan(); self.ack()
        self.assertIn('持续异常提醒', self.plan(now=4600)['channels']['telegram']['body'])
        self.ack(now=4610)
        self.assertEqual(self.plan(now=4700)['channels'], {})

    def test_new_issue_does_not_wait_for_existing_issue_cooldown(self):
        self.observed([self.issue()]); self.plan(); self.ack()
        self.observed([self.issue(), self.issue('disk:root', 'critical', 'disk 95%', 'host.disk')], checked=('host.cpu', 'host.disk'))
        plan = self.plan(now=1010)
        self.assertEqual(plan['channels']['telegram']['send_keys'], ['disk:root'])
        self.assertNotIn('CPU 90%', plan['channels']['telegram']['body'])

    def test_escalation_is_immediate(self):
        self.observed([self.issue()]); self.plan(); self.ack()
        self.observed([self.issue(severity='critical')])
        self.assertIn('级别变化', self.plan(now=1001)['channels']['telegram']['body'])

    def test_recovery_sent_once_only_after_ack(self):
        self.observed([self.issue()]); self.plan(); self.ack()
        self.observed()
        plan = self.plan(now=1001)
        self.assertIn('恢复', plan['channels']['telegram']['title'])
        self.assertIn('已恢复', plan['channels']['telegram']['body'])
        self.assertIn('telegram', self.plan(now=1002)['channels'])  # no ACK: retry recovery
        self.ack(now=1002)
        self.assertEqual(self.plan(now=1003)['channels'], {})

    def test_partial_recovery_keeps_other_active_issue_and_its_cooldown(self):
        cpu, disk = self.issue(), self.issue('disk:root', 'critical', 'disk 95%', 'host.disk')
        self.observed([cpu, disk], checked=('host.cpu', 'host.disk')); self.plan(); self.ack()
        self.observed([disk], checked=('host.cpu', 'host.disk'))
        plan = self.plan(now=1020)
        self.assertEqual(plan['channels']['telegram']['recover_keys'], ['host:cpu'])
        self.ack(now=1020)
        self.assertEqual(set(self.active()), {'disk:root'})
        self.assertEqual(self.active()['disk:root']['last_sent'], 1000)

    def test_probe_failure_and_skipped_checks_never_mean_recovery(self):
        self.observed([self.issue()]); self.plan(); self.ack()
        self.observed(checked=('host.memory',), unknown=('host.cpu',))
        self.assertEqual(self.plan(now=1010)['channels'], {})
        self.assertIn('host:cpu', self.active())

    def test_parent_unknown_scope_overrides_child_checked_scope(self):
        issue = self.issue('container:app', 'critical', 'app stopped', 'docker.containers')
        self.observed([issue], checked=('docker.containers',)); self.plan(); self.ack()
        self.observed(checked=('docker.containers',), unknown=('docker',))
        self.assertEqual(self.plan(now=1010)['channels'], {})

    def test_healthy_first_scan_does_not_send_recovery(self):
        self.observed()
        self.assertEqual(self.plan()['channels'], {})

    def test_failed_delivery_is_not_acknowledged_and_retries(self):
        self.observed([self.issue()])
        first = self.plan()
        second = self.plan(now=1001)
        self.assertEqual(first['channels']['telegram']['send_keys'], second['channels']['telegram']['send_keys'])
        self.assertFalse((self.directory / 'state.json').exists())

    def test_partial_channel_failure_retries_only_failed_channel(self):
        self.environment.update(UA_HEALTH_WEBHOOK='https://private.test/?key=SECRET', UA_HEALTH_WEBHOOK_KIND='wecom')
        self.observed([self.issue()]); self.plan(); self.ack('telegram')
        self.assertEqual(set(self.plan(now=1001)['channels']), {'webhook'})
        self.ack('webhook', 1001)
        self.assertEqual(self.plan(now=1002)['channels'], {})
        self.assertNotIn('SECRET', (self.directory / 'state.json').read_text())

    def test_recovery_partial_channel_failure_retries_only_failed_channel(self):
        self.environment.update(UA_HEALTH_WEBHOOK='https://private.test/', UA_HEALTH_WEBHOOK_KIND='wecom')
        self.observed([self.issue()]); self.plan(); self.ack('telegram'); self.ack('webhook')
        self.observed(); self.plan(now=1001); self.ack('telegram', 1001)
        self.assertEqual(set(self.plan(now=1002)['channels']), {'webhook'})

    def test_changed_channel_credentials_get_independent_dedup_state(self):
        self.observed([self.issue()]); self.plan(); self.ack()
        self.environment['UA_HEALTH_TG_TOKEN'] = 'new-token'
        self.assertIn('telegram', self.plan(now=1001)['channels'])

    def test_ignored_issue_is_retired_without_false_recovery(self):
        self.observed([self.issue()]); self.plan(); self.ack()
        self.observed(ignored=('host:cpu',))
        self.assertEqual(self.plan(now=1001)['channels'], {})
        self.assertFalse(self.active())

    def test_corrupt_state_is_preserved_and_refused(self):
        path = self.directory / 'state.json'
        path.write_text('{invalid'); path.chmod(0o600)
        self.observed([self.issue()])
        with self.assertRaises(self.mod.HealthError):
            self.plan()
        self.assertEqual(path.read_text(), '{invalid')

    def test_unsafe_directory_and_symlink_are_not_modified(self):
        unsafe = self.work / 'unsafe'
        unsafe.mkdir(mode=0o777); unsafe.chmod(0o777)
        with self.assertRaises(self.mod.HealthError):
            self.mod.prepare_state(unsafe)
        self.assertEqual(unsafe.stat().st_mode & 0o777, 0o777)
        link = self.work / 'link'
        link.symlink_to(self.directory, target_is_directory=True)
        with self.assertRaises(self.mod.HealthError):
            self.mod.prepare_state(link)

    def test_state_hardlink_is_refused_without_overwriting_target(self):
        protected = self.work / 'protected'
        protected.write_text('keep'); protected.chmod(0o600)
        os.link(protected, self.directory / 'state.json')
        with self.assertRaises(self.mod.HealthError):
            self.mod.load_state(self.directory)
        self.assertEqual(protected.read_text(), 'keep')

    def test_enospc_during_commit_preserves_previous_state(self):
        self.observed([self.issue()]); self.plan(); self.ack()
        state = self.directory / 'state.json'
        original = state.read_bytes()
        self.observed(); self.plan(now=1001)
        with mock.patch.object(self.mod.os, 'replace', side_effect=OSError(28, 'disk full')):
            with self.assertRaises(OSError):
                self.ack(now=1001)
        self.assertEqual(state.read_bytes(), original)
        self.assertIn('telegram', self.plan(now=1002)['channels'])

    def test_double_commit_rejects_stale_plan(self):
        self.observed([self.issue()]); self.plan(); self.ack()
        with self.assertRaises(self.mod.HealthError):
            self.ack(now=2000)
        self.assertEqual(self.active()['host:cpu']['last_sent'], 1000)

    def test_clock_rollback_does_not_suppress_forever(self):
        self.observed([self.issue()]); self.plan(); self.ack()
        self.assertIn('telegram', self.plan(now=900)['channels'])
        self.ack(now=900)
        self.assertEqual(self.plan(now=901)['channels'], {})

    def test_zero_cooldown_allows_every_scan(self):
        self.observed([self.issue()]); self.plan(); self.ack()
        self.assertIn('telegram', self.plan(now=1000, cooldown=0)['channels'])

    def test_empty_or_duplicate_observation_is_not_healthy(self):
        self.events.write_text('')
        with self.assertRaises(self.mod.HealthError):
            self.plan()
        self.observed([self.issue(), self.issue()])
        with self.assertRaises(self.mod.HealthError):
            self.plan()


class NotificationFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        (self.work / 'tmp').mkdir(mode=0o700)
        self.env = dict(os.environ, UA_BOX_HOME=str(self.work), UA_BOX_TMP_DIR=str(self.work / 'tmp'),
                        UA_NOTIFY_STATE_DIR=str(self.work / 'state'), UA_NOTIFY_TG_BOT_TOKEN='fake-token', UA_NOTIFY_TG_CHAT_ID='fake-chat',
                        UA_NOTIFY_WEBHOOK_URL='', SENT=str(self.work / 'sent'), READY=str(self.work / 'ready'),
                        FAIL_WEBHOOK='0', WAIT='0', UA_NOTIFY_COOLDOWN_SECONDS='3600')
        self.events(True)
        self.source = '\n'.join(base.shell_function(name) for name in ['ua_notify_serialized', 'ua_health_operation', 'ua_health_program'])
        self.source += r'''
has_cmd() { command -v "$1" >/dev/null; }
ua_abnormal_summary() { printf '%s\n' "$EVENTS" > "$UA_ALERT_EVENTS_FILE"; echo 'human report can change freely'; }
ua_webhook_kind() { echo wecom; }
ua_send_notification() {
  python3 -c 'import json,sys; print(json.dumps(sys.argv[1:]))' "$3" "$1" "$2" >> "$SENT"
  if [ "$WAIT" = 1 ]; then touch "$READY"; sleep 1; fi
  [ "$3" != webhook ] || [ "$FAIL_WEBHOOK" = 0 ]
}
ua_notify_serialized
'''

    def events(self, unhealthy):
        rows = [{'type': 'checked', 'scope': 'host.cpu'}]
        if unhealthy:
            rows.append({'type': 'issue', 'scope': 'host.cpu', 'key': 'host:cpu', 'severity': 'warning', 'message': 'CPU high'})
        self.env['EVENTS'] = '\n'.join(json.dumps(row) for row in rows)

    def run_flow(self):
        return subprocess.run(['/bin/bash', '-c', self.source], env=self.env, capture_output=True, text=True, timeout=15)

    def sent(self):
        path = self.work / 'sent'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def test_complete_flow_fault_dedup_recovery_then_quiet(self):
        for expected in [1, 1]:
            result = self.run_flow()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(self.sent()), expected)
        self.events(False)
        self.assertEqual(self.run_flow().returncode, 0)
        self.assertEqual(len(self.sent()), 2)
        self.assertIn('恢复', self.sent()[1][1])
        self.assertEqual(self.run_flow().returncode, 0)
        self.assertEqual(len(self.sent()), 2)

    def test_flow_partial_failure_does_not_repeat_successful_channel(self):
        self.env.update(UA_NOTIFY_WEBHOOK_URL='https://fake.invalid/', FAIL_WEBHOOK='1')
        self.assertEqual(self.run_flow().returncode, 1)
        self.assertEqual([row[0] for row in self.sent()], ['telegram', 'webhook'])
        self.env['FAIL_WEBHOOK'] = '0'
        self.assertEqual(self.run_flow().returncode, 0)
        self.assertEqual([row[0] for row in self.sent()], ['telegram', 'webhook', 'webhook'])

    def test_concurrent_cron_runs_send_only_once(self):
        self.env['WAIT'] = '1'
        first = subprocess.Popen(['/bin/bash', '-c', self.source], env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 8
            while not (self.work / 'ready').exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue((self.work / 'ready').exists())
            second = self.run_flow()
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertIn('已有巡检', second.stdout)
            out, err = first.communicate(timeout=10)
            self.assertEqual(first.returncode, 0, err)
            self.assertEqual(len(self.sent()), 1)
        finally:
            if first.poll() is None:
                first.terminate(); first.communicate(timeout=5)

    def test_sigint_and_sigterm_release_lock_and_allow_unacknowledged_retry(self):
        for sig in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(sig=sig), tempfile.TemporaryDirectory() as directory:
                environment = dict(self.env, UA_NOTIFY_STATE_DIR=directory + '/state', SENT=directory + '/sent',
                                   READY=directory + '/ready', WAIT='1')
                process = subprocess.Popen(['/bin/bash', '-c', self.source], env=environment,
                                           start_new_session=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                try:
                    deadline = time.monotonic() + 8
                    while not Path(environment['READY']).exists() and time.monotonic() < deadline:
                        time.sleep(0.01)
                    self.assertTrue(Path(environment['READY']).exists())
                    os.killpg(process.pid, sig)
                    process.communicate(timeout=5)
                    self.assertNotEqual(process.returncode, 0)
                    self.assertFalse((Path(environment['UA_NOTIFY_STATE_DIR']) / 'state.json').exists())
                    environment['WAIT'] = '0'
                    retry = subprocess.run(['/bin/bash', '-c', self.source], env=environment,
                                           capture_output=True, text=True, timeout=10)
                    self.assertEqual(retry.returncode, 0, retry.stderr)
                    self.assertNotIn('已有巡检', retry.stdout)
                    self.assertTrue((Path(environment['UA_NOTIFY_STATE_DIR']) / 'state.json').exists())
                finally:
                    if process.poll() is None:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.communicate(timeout=5)

    def test_test_notification_does_not_touch_dedup_state(self):
        code = base.shell_function('ua_status_notify') + '''
ua_send_notification() { echo TEST; }
ua_notify_serialized() { echo WRONG; return 1; }
ua_status_notify --test
'''
        result = subprocess.run(['/bin/bash', '-c', code], env=self.env, capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), 'TEST')
        self.assertFalse((self.work / 'state').exists())

    def test_corrupt_state_prevents_any_send(self):
        directory = self.work / 'state'
        directory.mkdir(mode=0o700)
        state = directory / 'state.json'
        state.write_text('broken'); state.chmod(0o600)
        result = self.run_flow()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.sent())
        self.assertEqual(state.read_text(), 'broken')

    def test_plan_read_error_is_not_mistaken_for_no_pending_notification(self):
        original = self.source
        self.source = original.rsplit('ua_notify_serialized\n', 1)[0] + '''
eval "$(declare -f ua_health_operation | sed '1s/ua_health_operation/real_health_operation/')"
ua_health_operation() {
  [ "$1" != plan-field ] || return 2
  real_health_operation "$@"
}
ua_notify_serialized
'''
        result = self.run_flow()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.sent())

    def test_sender_filters_channels_and_log_failure_does_not_undo_delivery_ack(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = Path(directory) / 'calls'
            code = base.shell_function('ua_send_notification') + '''
has_cmd() { return 0; }
ua_notification_post() { printf '%s\\n' "$1" >> "$CALLS"; }
ua_webhook_kind() { echo wecom; }
ua_health_operation() { echo '{}'; }
log_action() { return 1; }
ua_send_notification title body telegram || exit $?
ua_send_notification title body webhook
'''
            env = dict(self.env, CALLS=str(calls), UA_NOTIFY_WEBHOOK_URL='https://test.invalid/')
            result = subprocess.run(['/bin/bash', '-c', code], env=env, capture_output=True, text=True, timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(calls.read_text().splitlines(), ['telegram', 'wecom'])


class AlertObservationTests(unittest.TestCase):
    def setUp(self):
        self.mod = health_module()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        self.path = self.work / 'events'
        self.env = dict(os.environ, UA_ALERT_EVENTS_FILE=str(self.path), CPU='0', MEMORY='1', DISK='1',
                        DOCKER_ON='1', DAEMON_RC='0', INVENTORY_RC='0', INVENTORY='',
                        SYSTEMD_ON='0', SERVICE_RC='0', RCLONE_ON='0', REMOTES='remote:', REMOTE_LIST_RC='0',
                        REMOTE_RC='0', UA_IGNORE_CONTAINERS='', UA_IGNORE_SERVICES='', UA_IGNORE_RCLONE_REMOTES='',
                        UA_MONITOR_SERVICES='caddy', UA_STATUS_SKIP_RCLONE='0')
        self.source = '\n'.join(base.shell_function(name) for name in ['ua_abnormal_summary', 'list_has_word'])
        self.source += r'''
has_cmd() {
  case "$1" in
    docker) [ "$DOCKER_ON" = 1 ] ;;
    systemctl) [ "$SYSTEMD_ON" = 1 ] ;;
    rclone) [ "$RCLONE_ON" = 1 ] ;;
    timeout) return 0 ;;
    *) return 1 ;;
  esac
}
cpu_usage() { [ "$CPU" != fail ] || return 1; [ "$CPU" != partial-fail ] || { printf 0; return 1; }; printf '%s' "$CPU"; }
free() { printf 'Mem: 100 %s\n' "$MEMORY"; }
df() { printf 'Filesystem Blocks Used Available Use%% Mounted\nfake 100 1 99 %s%% /\n' "$DISK"; }
getconf() { echo 999999; }
docker() {
  case "$1" in
    info) return "$DAEMON_RC" ;;
    ps) [ "$INVENTORY_RC" = 0 ] || return "$INVENTORY_RC"; printf '%s' "$INVENTORY" ;;
    *) echo 'UNEXPECTED MUTATION' >&2; return 99 ;;
  esac
}
systemctl() {
  case "$1" in
    list-unit-files) echo 'caddy.service enabled' ;;
    is-active) return "$SERVICE_RC" ;;
    *) echo 'UNEXPECTED MUTATION' >&2; return 99 ;;
  esac
}
rclone() { [ "$1" = listremotes ] || return 99; printf '%s' "$REMOTES"; return "$REMOTE_LIST_RC"; }
timeout() { return "$REMOTE_RC"; }
ua_abnormal_summary
'''

    def collect(self):
        self.path.write_text('')
        result = subprocess.run(['/bin/bash', '-c', self.source], env=self.env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('UNEXPECTED MUTATION', result.stderr)
        return self.mod.observations(self.path)

    def previous(self, key, scope):
        issue = {'scope': scope, 'severity': 'critical', 'message': 'previous fault', 'first_seen': 1000, 'last_sent': 1000}
        identifier = 'a' * 64
        state = {'version': 1, 'channels': {identifier: {'kind': 'telegram', 'active': {key: issue}}}}
        return state, [{'kind': 'telegram', 'id': identifier}]

    def test_container_unhealthy_emits_stable_object_key(self):
        self.env['INVENTORY'] = 'app\trunning\tUp (unhealthy)\tproject\napp-ua-old-123\texited\tExited\tproject\n'
        observed = self.collect()
        self.assertEqual(set(observed['issues']), {'container:app', 'compose:project'})
        self.assertIn('docker.container:app', observed['checked'])
        self.assertNotIn('docker.container:app-ua-old-123', observed['checked'])

    def test_removed_container_does_not_emit_recovery(self):
        observed = self.collect()
        state, channels = self.previous('container:app', 'docker.container:app')
        plan = self.mod.notification_plan(state, observed, channels, 3600, 1001)
        self.assertEqual(plan['channels'], {})
        self.assertIn('docker.container:app', plan['unknown_scopes'])

    def test_healthy_existing_container_emits_recovery(self):
        self.env['INVENTORY'] = 'app\trunning\tUp (healthy)\tproject\n'
        state, channels = self.previous('container:app', 'docker.container:app')
        plan = self.mod.notification_plan(state, self.collect(), channels, 3600, 1001)
        self.assertEqual(plan['channels']['telegram']['recover_keys'], ['container:app'])

    def test_starting_health_is_unknown_and_cannot_recover(self):
        self.env['INVENTORY'] = 'app\trunning\tUp (health: starting)\t\n'
        observed = self.collect()
        self.assertIn('docker.container:app', observed['unknown'])
        state, channels = self.previous('container:app', 'docker.container:app')
        self.assertEqual(self.mod.notification_plan(state, observed, channels, 3600, 1001)['channels'], {})

    def test_failed_inventory_is_unknown_not_healthy(self):
        self.env['INVENTORY_RC'] = '1'
        observed = self.collect()
        self.assertIn('docker.container', observed['unknown'])
        self.assertIn('probe:docker.inventory', observed['issues'])
        state, channels = self.previous('container:app', 'docker.container:app')
        plan = self.mod.notification_plan(state, observed, channels, 3600, 1001)
        self.assertFalse(plan['channels']['telegram']['recover_keys'])

    def test_partial_container_row_never_recovers_previous_fault(self):
        self.env['INVENTORY'] = 'app\trunning\n'
        observed = self.collect()
        self.assertIn('docker.container:app', observed['unknown'])
        state, channels = self.previous('container:app', 'docker.container:app')
        plan = self.mod.notification_plan(state, observed, channels, 3600, 1001)
        self.assertFalse(plan['channels']['telegram']['recover_keys'])

    def test_daemon_failure_cannot_recover_previous_containers(self):
        self.env['DAEMON_RC'] = '1'
        observed = self.collect()
        self.assertIn('docker:daemon', observed['issues'])
        state, channels = self.previous('container:app', 'docker.container:app')
        plan = self.mod.notification_plan(state, observed, channels, 3600, 1001)
        self.assertFalse(plan['channels']['telegram']['recover_keys'])

    def test_ignored_container_is_explicitly_retired(self):
        self.env.update(INVENTORY='app\texited\tExited\tproject\n', UA_IGNORE_CONTAINERS='app')
        observed = self.collect()
        self.assertIn('container:app', observed['ignored'])
        self.assertNotIn('container:app', observed['issues'])
        self.assertNotIn('compose:project', observed['issues'])

    def test_disk_warning_and_critical_share_identity(self):
        self.env['DISK'] = '85'
        warning = self.collect()['issues']['disk:root']
        self.env['DISK'] = '95'
        critical = self.collect()['issues']['disk:root']
        self.assertEqual(warning['severity'], 'warning')
        self.assertEqual(critical['severity'], 'critical')
        self.assertEqual(warning['scope'], critical['scope'])

    def test_failed_cpu_probe_is_unknown_not_zero_usage(self):
        for value in ['fail', 'partial-fail']:
            with self.subTest(value=value):
                self.env['CPU'] = value
                observed = self.collect()
                self.assertIn('host.cpu', observed['unknown'])
                self.assertNotIn('host.cpu', observed['checked'])

    def test_skipped_rclone_and_removed_remote_cannot_recover(self):
        state, channels = self.previous('rclone:remote', 'rclone:remote')
        for updates in [dict(RCLONE_ON='1', UA_STATUS_SKIP_RCLONE='1'), dict(RCLONE_ON='1', REMOTES='')]:
            with self.subTest(updates=updates):
                self.env.update(updates)
                plan = self.mod.notification_plan(state, self.collect(), channels, 3600, 1001)
                self.assertEqual(plan['channels'], {})

    def test_removed_but_explicitly_ignored_remote_is_retired(self):
        self.env.update(UA_IGNORE_RCLONE_REMOTES='remote', RCLONE_ON='0')
        observed = self.collect()
        self.assertIn('rclone:remote', observed['ignored'])

    def test_rclone_timeout_fault_and_success_are_distinguished(self):
        self.env.update(RCLONE_ON='1', REMOTE_RC='124')
        self.assertIn('rclone:remote', self.collect()['issues'])
        self.env['REMOTE_RC'] = '0'
        observed = self.collect()
        self.assertNotIn('rclone:remote', observed['issues'])
        self.assertIn('rclone:remote', observed['checked'])

    def test_unknown_service_status_cannot_recover(self):
        self.env.update(SYSTEMD_ON='1', SERVICE_RC='4')
        observed = self.collect()
        state, channels = self.previous('service:caddy', 'service:caddy')
        self.assertEqual(self.mod.notification_plan(state, observed, channels, 3600, 1001)['channels'], {})
        self.env['SERVICE_RC'] = '0'
        plan = self.mod.notification_plan(state, self.collect(), channels, 3600, 1001)
        self.assertEqual(plan['channels']['telegram']['recover_keys'], ['service:caddy'])
