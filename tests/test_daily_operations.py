"""Daily-operation regression tests: temporary files and simulated commands only.

No Docker socket, production service, host firewall or production database is used.
"""
import copy
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import unittest
from unittest import mock

import test_regressions as base


class DailyShellTests(unittest.TestCase):
    def bash(self, functions, code, stdin='', env=None):
        source = '\n'.join(base.shell_function(f) for f in functions)
        return subprocess.run(['bash', '-c', source + '\n' + code], input=stdin,
                              text=True, capture_output=True, timeout=10, env=env)

    def test_container_selection_returns_immutable_id(self):
        result = self.bash(['docker_select_container', 'select_with_fzf_or_menu'], '''
has_cmd() { return 1; }
docker() { printf 'aaa web Up 1 day\nbbb db Exited\n'; }
docker_select_container choose
''', '2\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'bbb')
        self.assertIn('web', result.stderr)

    def test_empty_cancel_and_failed_listing_never_select(self):
        for fake, choice in [('return 0', '1\n'), ("echo 'aaa web Up'", '0\n'), ('return 1', '')]:
            with self.subTest(fake=fake):
                result = self.bash(['docker_select_container', 'select_with_fzf_or_menu'],
                                   'has_cmd() { return 1; }; docker() { ' + fake + '; }; docker_select_container choose', choice)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, '')

    def batch(self, action, confirm=True, listing=None):
        listing = listing if listing is not None else (
            'a\tweb\trunning\t\nb\tweb-ua-old-123\texited\t\n'
            'c\tweb-ua-failed-123\texited\t\nd\tdb\texited\t\n'
            'e\tmanaged\texited\toperation\n')
        return self.bash(['docker_container_all_action'], '''
docker() {
  if [ "$1" = ps ]; then printf '%s' "$INVENTORY"; else printf 'EXEC'; printf ' <%s>' "$@"; fi
}
confirm_danger() { return ''' + ('0' if confirm else '1') + '''; }
log_action() { :; }
docker_container_all_action ''' + action,
                         env=dict(os.environ, INVENTORY=listing))

    def test_start_all_excludes_retained_and_managed_stopped_containers(self):
        result = self.batch('start')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('EXEC <start> <d>', result.stdout)
        self.assertNotIn('<b>', result.stdout)
        self.assertNotIn('<c>', result.stdout)
        self.assertNotIn('<e>', result.stdout)

    def test_batch_stop_restart_delete_use_only_previewed_targets(self):
        for action, expected in [('stop', 'EXEC <stop> <a>'), ('restart', 'EXEC <restart> <a>'), ('rm', 'EXEC <rm> <-f> <a> <d> <e>')]:
            with self.subTest(action=action):
                result = self.batch(action)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(expected, result.stdout)
                self.assertNotIn('<b>', result.stdout)

    def test_cancelled_batch_has_no_action(self):
        self.assertNotIn('EXEC', self.batch('restart', False).stdout)

    def test_batch_listing_failure_never_executes_action(self):
        result = self.bash(['docker_container_all_action'], 'docker() { return 1; }; docker_container_all_action start')
        self.assertNotEqual(result.returncode, 0)

    def test_port_validation_rejects_zero_overflow_and_shell_input(self):
        for value in ['0', '65536', '-1', '999999999999999999999999', '80;echo BAD', '']:
            result = self.bash(['valid_port'], 'valid_port "$VALUE"', env=dict(os.environ, VALUE=value))
            self.assertNotEqual(result.returncode, 0, value)
        for value in ['1', '65535', '080']:
            self.assertEqual(self.bash(['valid_port'], 'valid_port "$VALUE"', env=dict(os.environ, VALUE=value)).returncode, 0)

    def test_container_commands_are_not_written_to_private_log(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / 'audit.log'
            result = self.bash(['docker_container_create', 'log_action'], '''
sh() { :; }
docker_container_create
''', '-e PASSWORD=top-secret -e TOKEN=hidden app\ny\n',
                               dict(os.environ, UA_BOX_LOG_DIR=directory, UA_BOX_LOG_FILE=str(log)))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn('top-secret', log.read_text())
            self.assertNotIn('hidden', log.read_text())
            self.assertEqual(log.stat().st_mode & 0o777, 0o600)

    def test_health_monitor_reports_unhealthy_and_ignores_retained(self):
        result = self.bash(['collect_bad_containers', 'list_has_word'], '''
UA_IGNORE_CONTAINERS=ignored
has_cmd() { return 0; }
docker() {
  [ "$1" = info ] && return 0
  printf 'healthy\trunning\tUp (healthy)\nbroken\trunning\tUp (unhealthy)\nstopped\texited\tExited\nignored\texited\tExited\napp-ua-old-123\texited\tExited\n'
}
collect_bad_containers
''')
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.splitlines(), ['broken', 'stopped'])

    def test_compose_summary_excludes_retained_old_instances(self):
        result = self.bash(['get_compose_projects_summary'], '''
has_cmd() { return 0; }
docker() { [ "$1" = info ] && return 0; printf 'web\tapp\trunning\nweb-ua-old-123\tapp\texited\n'; }
get_compose_projects_summary
''')
        self.assertEqual(result.returncode, 0)
        self.assertIn('app (1/1 运行)', result.stdout)

    def test_actual_alert_flow_reports_unhealthy_without_retained_false_alarm(self):
        result = self.bash(['ua_abnormal_summary', 'collect_bad_containers', 'list_has_word'], '''
has_cmd() { [ "$1" = docker ]; }
cpu_usage() { echo 0; }; free() { echo 'Mem: 100 1'; }
df() { printf 'Filesystem Blocks Used Available Use%% Mounted\nfake 100 1 99 1%% /\n'; }
getconf() { echo 999999; }
docker() {
  [ "$1" = info ] && return 0
  case "$*" in
    *'{{.Status}}'*) printf 'web\trunning\tUp (unhealthy)\nweb-ua-old-123\texited\tExited\n' ;;
    *) printf 'web\tapp\trunning\nweb-ua-old-123\tapp\texited\n' ;;
  esac
}
ua_abnormal_summary
''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Docker 异常容器', result.stdout)
        self.assertIn('\nweb\n', result.stdout)
        self.assertNotIn('web-ua-old-', result.stdout)
        self.assertNotIn('暂无明显异常', result.stdout)
        self.assertNotIn('Compose 项目有容器已停止', result.stdout)

    def test_panel_creation_has_scoped_filebrowser_mount_and_private_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            replacements = {'/root/filebrowser': str(work / 'filebrowser'),
                            '/root/nginx-proxy-manager': str(work / 'npm'), '/root/openlist': str(work / 'openlist')}
            stubs = '''
need_root() { :; }; docker_require() { :; }; docker_container_exists() { return 1; }
is_port_taken() { return 1; }; ask_port_bind_ip() { echo '127.0.0.1:'; }
ask_deploy_mode() { echo 2; }; log_action() { :; }; check_and_prompt_caddy_proxy() { :; }
get_display_ip() { echo '127.0.0.1'; }; npm_container_name() { echo npm; }; sleep() { :; }
docker() { printf '%s\\n' "$*" >> "$CALLS"; }
'''
            for name, stdin in [('filebrowser_install', '\n\n'), ('npm_install', '\n\n\n'), ('openlist_install', '\n\n')]:
                source = base.shell_function(name)
                for original, isolated in replacements.items():
                    source = source.replace(original, isolated)
                result = self.bash(['valid_port'], source + '\n' + stubs + name, stdin,
                                   dict(os.environ, CALLS=str(work / 'commands')))
                self.assertEqual(result.returncode, 0, result.stderr)
            compose = (work / 'filebrowser/docker-compose.yml').read_text()
            self.assertIn(str(work / 'filebrowser/files') + ':/srv', compose)
            self.assertNotIn("'/:/srv'", compose)
            for path in [work / 'npm', work / 'npm/data', work / 'npm/letsencrypt', work / 'openlist/data']:
                self.assertEqual(path.stat().st_mode & 0o777, 0o750)

    def test_rclone_existing_unit_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            mount = work / 'mnt'
            mount.mkdir()
            name = subprocess.check_output(['systemd-escape', '--path', '--', str(mount)], text=True).strip()
            unit = work / ('rclone-mount-' + name + '.service')
            unit.write_text('original unit')
            source = base.shell_function('rclone_mount_systemd').replace('/etc/systemd/system', str(work))
            result = self.bash([], source + '''
need_root() { :; }; rclone_require() { :; }; has_cmd() { return 0; }; mountpoint() { return 1; }
run_recovery_operation() { echo WRONG; }; systemctl() { echo WRONG; }
rclone_mount_systemd
''', 'remote:path\n' + str(mount) + '\n\n')
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(unit.read_text(), 'original unit')
            self.assertNotIn('WRONG', result.stdout)

    def test_notification_transport_checks_body_and_cleans_only_its_response(self):
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            program = work / 'verify.py'
            program.write_text(base.SCRIPT.split("<<'PY_UA_RECOVERY'\n", 1)[1].split('\nPY_UA_RECOVERY', 1)[0])
            keep = work / 'keep'
            keep.write_text('user file')
            for reply, expected in [('{"errcode":1}', 1), ('{"errcode":0}', 0), ('bad-json', 1)]:
                result = self.bash(['ua_notification_post'], '''
curl() {
  while [ "$#" -gt 0 ]; do
    if [ "$1" = --output ]; then printf '%s' "$REPLY" > "$2"; shift 2; else shift; fi
  done
  printf 200
}
run_recovery_operation() { python3 "$VERIFY_PROGRAM" "$@"; }
ua_notification_post wecom https://test.invalid '{"text":"test"}'
''', env=dict(os.environ, UA_BOX_TMP_DIR=directory, REPLY=reply, VERIFY_PROGRAM=str(program)))
                self.assertEqual(result.returncode, expected, result.stderr)
                self.assertFalse(list(work.glob('notify.*')))
                self.assertEqual(keep.read_text(), 'user file')

    def test_menu_eof_exits_without_an_operation(self):
        for menu in ['main_menu', 'docker_container_menu']:
            result = self.bash([menu], '''
clear() { :; }; docker_require() { :; }; docker_container_list() { :; }
''' + menu)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.count('请输入'), 0)

    def firewall(self, mode, unavailable='', fail_insert=False, source=''):
        with tempfile.TemporaryDirectory() as directory:
            env = dict(os.environ, FW_DIR=directory, UNAVAILABLE=unavailable,
                       FAIL_INSERT=str(int(fail_insert)))
            result = self.bash(['docker_container_port_access', 'valid_port'], '''
need_root() { :; }; log_action() { :; }
has_cmd() { [ "$1" != "$UNAVAILABLE" ]; }
fake_fw() {
  local tool="$1"; shift
  printf '%s %s\n' "$tool" "$*" >> "$FW_DIR/calls"
  case "$2" in
    -S) return 0 ;;
    -C) [ -f "$FW_DIR/$tool" ] ;;
    -I) [ "$FAIL_INSERT" = 0 ] || return 1; touch "$FW_DIR/$tool" ;;
    -D) rm -f "$FW_DIR/$tool" ;;
    *) return 2 ;;
  esac
}
iptables() { fake_fw iptables "$@"; }
ip6tables() { fake_fw ip6tables "$@"; }
docker_container_port_access ''' + mode, '3000\ntcp\n' + source + '\n', env)
            calls = (Path(directory) / 'calls').read_text() if (Path(directory) / 'calls').exists() else ''
            rules = {p.name for p in Path(directory).iterdir() if p.name in ('iptables', 'ip6tables')}
            return result, calls, rules

    def test_firewall_both_families_are_applied_and_verified(self):
        result, calls, rules = self.firewall('close')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(rules, {'iptables', 'ip6tables'})
        self.assertIn('IPv6: 已核对', result.stdout)

    def test_firewall_missing_ipv6_aborts_before_ipv4_changes(self):
        result, calls, rules = self.firewall('close', 'ip6tables')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(rules)
        self.assertNotIn('-I', calls)

    def test_firewall_failed_insert_never_reports_success(self):
        result, _, rules = self.firewall('close', fail_insert=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(rules)
        self.assertNotIn('已核对', result.stdout)

    def test_firewall_ipv6_source_does_not_change_ipv4(self):
        result, calls, rules = self.firewall('close', source='2001:db8::/32')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(rules, {'ip6tables'})

    def test_ssh_firewall_error_is_returned(self):
        result = self.bash(['ssh_allow_port_firewall', 'valid_port'], '''
has_cmd() { [ "$1" = ufw ]; }
ufw() { if [ "$1" = status ]; then echo 'Status: active'; else return 1; fi; }
log_action() { echo WRONG; }
ssh_allow_port_firewall 2222
''')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('WRONG', result.stdout)

    def test_rclone_service_name_is_collision_free(self):
        names = [subprocess.check_output(['systemd-escape', '--path', '--', name], text=True).strip()
                 for name in ['/srv/a/b', '/srv/a-b', '/srv/a b']]
        self.assertEqual(len(set(names)), 3)

    def test_tmp_cleanup_preserves_user_parent_on_exit_and_signals(self):
        prefix = base.SCRIPT.split("gl_hui=", 1)[0]
        for sig, expected in [(None, 0), (signal.SIGINT, 130), (signal.SIGTERM, 143)]:
            with self.subTest(sig=sig), tempfile.TemporaryDirectory() as directory:
                keep = Path(directory) / 'user-file'
                keep.write_text('keep')
                code = prefix + '\nprintf "%s\\n" "$UA_BOX_TMP_DIR"\n' + ('read -r waiting\n' if sig else 'exit 0\n')
                process = subprocess.Popen(['bash', '-c', code], env=dict(os.environ, UA_BOX_TMP_DIR=directory),
                                           stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                owned = Path(process.stdout.readline().strip())
                if sig:
                    process.send_signal(sig)
                process.communicate(timeout=5)
                self.assertEqual(process.returncode, expected)
                self.assertEqual(keep.read_text(), 'keep')
                self.assertFalse(owned.exists())


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.mod = base.embedded_module('daily-config')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        self.path = self.work / 'daemon.json'
        self.original = b'{"data-root":"/custom","log-opts":{"labels":"service"},"ipv6":true}\n'
        self.path.write_bytes(self.original)
        self.path.chmod(0o640)
        lookup = mock.patch.object(self.mod.shutil, 'which', side_effect=lambda command: '/simulated/' + command)
        lookup.start()
        self.addCleanup(lookup.stop)
        self.commands = []
        self.mod.signals = mock.MagicMock(side_effect=lambda *a: __import__('contextlib').nullcontext())
        def run(command, **kwargs):
            self.commands.append(command)
            return subprocess.CompletedProcess(command, 0)
        self.mod.subprocess = mock.Mock(run=run)

    def configure(self):
        return self.mod.configure_daemon(str(self.path), '{"log-driver":"json-file","log-opts":{"max-size":"50m","max-file":"3"}}')

    def test_daemon_merge_preserves_unrelated_and_nested_options(self):
        self.configure()
        config = json.loads(self.path.read_text())
        self.assertEqual(config['data-root'], '/custom')
        self.assertTrue(config['ipv6'])
        self.assertEqual(config['log-opts'], {'labels': 'service', 'max-size': '50m', 'max-file': '3'})
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o640)
        backups = list(self.work.glob('*.bak'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), self.original)
        self.assertEqual(backups[0].stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.commands[-1], ['systemctl', 'is-active', '--quiet', 'docker'])

    def test_invalid_original_and_wrong_type_are_never_overwritten(self):
        for content in [b'{broken', b'[]', b'{"log-opts":false}']:
            with self.subTest(content=content):
                self.path.write_bytes(content)
                with self.assertRaises(Exception):
                    self.configure()
                self.assertEqual(self.path.read_bytes(), content)
        self.assertFalse(self.commands)

    def test_missing_dockerd_refuses_before_changes(self):
        self.mod.shutil.which = lambda _: None
        with self.assertRaises(self.mod.RecoveryError):
            self.configure()
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_invalid_staged_configuration_does_not_restart(self):
        self.mod.subprocess.run = lambda command, **kwargs: subprocess.CompletedProcess(command, 1)
        with self.assertRaises(self.mod.RecoveryError):
            self.configure()
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertFalse(list(self.work.glob('*.bak')))

    def test_failed_restart_restores_exact_original_and_retains_backup(self):
        states = iter([self.mod.RecoveryError('restart failed'), None])
        def restart():
            error = next(states)
            if error:
                raise error
        self.mod.restart_docker_service = mock.Mock(side_effect=restart)
        with self.assertRaisesRegex(self.mod.RecoveryError, '已恢复原配置并核验'):
            self.configure()
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertEqual(self.mod.restart_docker_service.call_count, 2)
        self.assertEqual(next(self.work.glob('*.bak')).read_bytes(), self.original)

    def test_failed_rollback_restart_is_not_success(self):
        self.mod.restart_docker_service = mock.Mock(side_effect=self.mod.RecoveryError('fail'))
        with self.assertRaisesRegex(self.mod.RecoveryError, '服务恢复未确认'):
            self.configure()
        self.assertEqual(self.path.read_bytes(), self.original)

    def test_daemon_write_enospc_preserves_original(self):
        self.mod.atomic_bytes = mock.Mock(side_effect=OSError(28, 'disk full'))
        self.mod.restart_docker_service = mock.Mock()
        with self.assertRaisesRegex(self.mod.RecoveryError, '未重启 Docker'):
            self.configure()
        self.assertEqual(self.path.read_bytes(), self.original)
        self.mod.restart_docker_service.assert_not_called()

    def test_daemon_failure_after_publish_also_restores_original(self):
        real = self.mod.atomic_bytes
        def interrupted(path, content, info=None):
            real(path, content, info)
            if content != self.original:
                raise OSError(28, 'fsync failed after rename')
        self.mod.atomic_bytes = interrupted
        self.mod.restart_docker_service = mock.Mock()
        with self.assertRaisesRegex(self.mod.RecoveryError, '原配置已核对；未重启'):
            self.configure()
        self.assertEqual(self.path.read_bytes(), self.original)
        self.assertEqual(next(self.work.glob('*.bak')).read_bytes(), self.original)
        self.mod.restart_docker_service.assert_not_called()

    def test_new_daemon_config_enospc_never_restarts_service(self):
        self.path.unlink()
        self.mod.atomic_bytes = mock.Mock(side_effect=OSError(28, 'disk full'))
        self.mod.restart_docker_service = mock.Mock()
        with self.assertRaisesRegex(self.mod.RecoveryError, '未重启 Docker'):
            self.configure()
        self.assertFalse(self.path.exists())
        self.mod.restart_docker_service.assert_not_called()

    def test_new_daemon_config_removed_if_restart_fails(self):
        self.path.unlink()
        self.mod.restart_docker_service = mock.Mock(side_effect=[RuntimeError('failure'), None])
        with self.assertRaises(self.mod.RecoveryError):
            self.configure()
        self.assertFalse(self.path.exists())

    def test_daemon_symlink_never_followed(self):
        other = self.work / 'private'
        other.write_text('keep')
        self.path.unlink()
        self.path.symlink_to(other)
        with self.assertRaises(self.mod.RecoveryError):
            self.configure()
        self.assertEqual(other.read_text(), 'keep')

    def test_fail2ban_changes_only_sshd_and_preserves_permissions(self):
        jail = self.work / 'jail.local'
        jail.write_text('[DEFAULT]\nport = default\n[sshd]\nenabled = true\nport = ssh\n[nginx]\nport = http,https\n')
        jail.chmod(0o640)
        self.mod.update_fail2ban_port(str(jail), 'ssh,2222')
        self.assertEqual(jail.read_text(), '[DEFAULT]\nport = default\n[sshd]\nenabled = true\nport = ssh,2222\n[nginx]\nport = http,https\n')
        self.assertEqual(jail.stat().st_mode & 0o777, 0o640)

    def test_fail2ban_missing_port_is_added_only_to_sshd(self):
        jail = self.work / 'jail.local'
        jail.write_text('[sshd]\nenabled = true\n[nginx]\nport = http\n')
        self.mod.update_fail2ban_port(str(jail), '2222')
        self.assertIn('enabled = true\nport = 2222\n[nginx]', jail.read_text())
        self.assertTrue(jail.read_text().endswith('port = http\n'))

    def test_rclone_unit_escapes_special_arguments(self):
        unit = self.work / 'mount.service'
        self.mod.write_rclone_unit(str(unit), '/usr/bin/rclone', 'remote:path with % and $',
                                   '/mnt/a b', '/private/config', '--header "x: a b"')
        content = unit.read_text()
        self.assertIn('"remote:path with %% and $$"', content)
        self.assertIn('"--header" "x: a b"', content)
        self.assertEqual(unit.stat().st_mode & 0o777, 0o600)

    def test_rclone_unit_rejects_newline_injection(self):
        unit = self.work / 'mount.service'
        with self.assertRaises(self.mod.RecoveryError):
            self.mod.write_rclone_unit(str(unit), '/usr/bin/rclone', 'remote:x\nExecStart=/bin/evil', '/mnt/x', '/config', '')
        self.assertFalse(unit.exists())

    def test_rclone_unit_passes_actual_systemd_parser_without_starting_service(self):
        unit = self.work / 'isolated-rclone.service'
        self.mod.write_rclone_unit(str(unit), '/bin/true', 'remote:some path%', '/mnt/test dir', '/tmp/config', '--header "a: b"')
        result = subprocess.run(['systemd-analyze', 'verify', '--man=no', str(unit)], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_notification_business_errors_and_invalid_json_fail(self):
        for kind, status, body in [('telegram', 200, '{"ok":false}'), ('wecom', 200, '{"errcode":1}'),
                                   ('feishu', 200, '{"code":1}'), ('dingtalk', 200, '{"errcode":400}'),
                                   ('bark', 200, '{"code":400}'), ('discord', 400, '{}'),
                                   ('wecom', 200, '{}'), ('feishu', 200, 'not-json'), ('telegram', 500, '{"ok":true}')]:
            with self.subTest(kind=kind, body=body), self.assertRaises(Exception):
                self.mod.verify_notification(kind, str(status), body)

    def test_notification_success_contracts(self):
        for kind, status, body in [('telegram', 200, '{"ok":true}'), ('wecom', 200, '{"errcode":0}'),
                                   ('feishu', 200, '{"code":0}'), ('feishu', 200, '{"StatusCode":0}'),
                                   ('dingtalk', 200, '{"errcode":0}'), ('bark', 200, '{"code":200}'),
                                   ('discord', 204, '')]:
            with self.subTest(kind=kind):
                self.mod.verify_notification(kind, str(status), body)

    def test_notification_payloads_match_each_platform(self):
        body = 'quote " and newline\n正文'
        self.assertEqual(self.mod.notification_payload('feishu', 'title', body), {'msg_type': 'text', 'content': {'text': body}})
        self.assertEqual(self.mod.notification_payload('wecom', 'title', body)['text']['content'], body)
        self.assertEqual(self.mod.notification_payload('dingtalk', 'title', body)['text']['content'], body)
        self.assertEqual(self.mod.notification_payload('discord', 'title', body), {'content': body})
        self.assertEqual(self.mod.notification_payload('bark', 'title', body), {'title': 'title', 'body': body})


class PortBindingTests(unittest.TestCase):
    def setUp(self):
        base.ContainerTests.setUp(self)
        self.old['HostConfig']['PortBindings']['5245/tcp'] = [{'HostIp': '127.0.0.1', 'HostPort': '5245'}]
        self.fake.containers[self.old['Id']] = copy.deepcopy(self.old)
        self.fake.after_start = lambda container: container['NetworkSettings'].update(Ports=copy.deepcopy(container['HostConfig']['PortBindings']))

    def bind(self):
        return self.mod.rebind_container('app', '8888', '80', '0.0.0.0', self.tmp.name, '5')

    def journal(self):
        return Path(self.tmp.name) / '.container-bind-app.json'

    def compose(self):
        self.compose_path = Path(self.tmp.name) / 'compose.yml'
        self.compose_path.write_text("services:\n  app:\n    image: app:latest\n    ports:\n      - '127.0.0.1:8888:80' # keep\n  other:\n    image: other:1\n")
        self.compose_path.chmod(0o640)
        self.compose_original = self.compose_path.read_bytes()
        labels = self.fake.lookup('app')['Config']['Labels']
        labels.update({'com.docker.compose.project': 'my-project', 'com.docker.compose.service': 'app',
                       'com.docker.compose.project.working_dir': self.tmp.name,
                       'com.docker.compose.project.config_files': str(self.compose_path)})
        before = {'services': {'app': {'image': 'app:latest', 'ports': [{'target': 80, 'published': '8888', 'host_ip': '127.0.0.1', 'protocol': 'tcp'}]},
                               'other': {'image': 'other:1'}}}
        def config(old, source):
            value = copy.deepcopy(before)
            if '0.0.0.0:8888:80' in Path(source).read_text():
                value['services']['app']['ports'][0]['host_ip'] = '0.0.0.0'
            return value
        self.mod.compose_config = mock.Mock(side_effect=config)

    def test_binding_preserves_all_settings_volumes_image_and_extra_ports(self):
        self.bind()
        formal = self.fake.lookup('app')
        self.assertEqual(formal['Image'], self.old['Image'])
        self.assertEqual(formal['Config']['Env'], self.old['Config']['Env'])
        self.assertEqual(formal['Config']['Labels']['ua.recovery.image_ref'], self.old['Config']['Image'])
        self.assertEqual(formal['HostConfig']['Memory'], self.old['HostConfig']['Memory'])
        self.assertEqual(formal['HostConfig']['PortBindings']['5245/tcp'], self.old['HostConfig']['PortBindings']['5245/tcp'])
        self.assertEqual(formal['HostConfig']['PortBindings']['80/tcp'], [{'HostIp': '0.0.0.0', 'HostPort': '8888'}])
        self.assertIn('anonymous123:/database:rw', formal['HostConfig']['Binds'])
        self.assertFalse(self.fake.lookup(self.old['Id'])['State']['Running'])
        self.assertEqual(self.fake.lookup(self.old['Id'])['HostConfig']['RestartPolicy'], self.mod.NO_RESTART)
        self.assertFalse(any(call[0] in ('rm', 'pull') for call in self.calls))
        self.assertEqual(json.loads(self.journal().read_text())['state'], 'committed')

    def test_failed_candidate_start_recovers_old_actual_state(self):
        self.fake.fail_start = True
        with self.assertRaises(Exception):
            self.bind()
        self.assertEqual(self.fake.lookup('app')['Id'], self.old['Id'])
        self.assertTrue(self.fake.lookup('app')['State']['Running'])
        self.assertEqual(self.fake.lookup('app')['HostConfig']['PortBindings'], self.old['HostConfig']['PortBindings'])
        self.assertEqual(json.loads(self.journal().read_text())['state'], 'recovered')

    def test_actual_binding_not_published_rolls_back(self):
        self.fake.after_start = None
        with self.assertRaisesRegex(self.mod.RecoveryError, '实际端口绑定未确认'):
            self.bind()
        self.assertEqual(self.fake.lookup('app')['Id'], self.old['Id'])

    def test_sigint_and_sigterm_after_rename_recover_by_id(self):
        for sig in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(sig=sig):
                # Signals are injected into the transaction seam, never a real container.
                self.mod.fault = lambda point: (_ for _ in ()).throw(self.mod.Interrupted(str(sig))) if point == 'after_container_rename' else None
                with self.assertRaises(self.mod.Interrupted):
                    self.bind()
                self.assertTrue(self.fake.lookup('app')['State']['Running'])
                self.assertEqual(self.fake.lookup('app')['Id'], self.old['Id'])
                self.assertEqual(json.loads(self.journal().read_text())['state'], 'recovered')

    def test_repeated_recovery_keeps_one_running_instance(self):
        self.bind()
        self.mod.recover_container(self.journal())
        self.mod.recover_container(self.journal())
        self.assertEqual(self.fake.running_ids(), {self.old['Id']})
        self.assertEqual(self.fake.resource_conflicts(), [])

    def test_missing_mapping_refuses_before_stop(self):
        with self.assertRaises(self.mod.RecoveryError):
            self.mod.rebind_container('app', '9999', '80', '0.0.0.0', self.tmp.name, '5')
        self.assertFalse(any(call[0] == 'stop' for call in self.calls))
        self.assertFalse(self.fake.new_ids)

    def test_binding_journal_disk_full_never_stops_original(self):
        self.mod.save_json = mock.Mock(side_effect=OSError(28, 'journal disk full'))
        with self.assertRaises(OSError):
            self.bind()
        self.assertTrue(self.fake.lookup('app')['State']['Running'])
        self.assertFalse(any(call[0] == 'stop' for call in self.calls))

    def test_unfinished_binding_blocks_image_update(self):
        self.journal().write_text('{"state":"recovery_incomplete"}')
        with self.assertRaisesRegex(self.mod.RecoveryError, '未完成的端口切换'):
            self.mod.update_container('app', self.tmp.name, '5')
        self.assertFalse(any(call[0] in ('stop', 'pull') for call in self.calls))

    def test_compose_stages_checks_and_changes_only_selected_service(self):
        self.compose()
        self.bind()
        self.assertIn('0.0.0.0:8888:80', self.compose_path.read_text())
        self.assertIn('# keep', self.compose_path.read_text())
        self.assertTrue(self.compose_path.read_text().endswith('  other:\n    image: other:1\n'))
        self.assertEqual(self.compose_path.stat().st_mode & 0o777, 0o640)
        self.assertEqual(self.mod.compose_config.call_count, 2)
        self.mod.recover_container(self.journal())
        self.assertEqual(self.compose_path.read_bytes(), self.compose_original)

    def test_compose_failure_restores_file_and_original_container(self):
        self.compose()
        self.fake.fail_create = True
        with self.assertRaises(Exception):
            self.bind()
        self.assertEqual(self.compose_path.read_bytes(), self.compose_original)
        self.assertTrue(self.fake.lookup('app')['State']['Running'])

    def test_compose_unexpected_changes_are_refused_before_any_mutation(self):
        self.compose()
        real = self.mod.compose_config.side_effect
        def unsafe(old, source):
            data = real(old, source)
            if str(source) != str(self.compose_path):
                data['services']['other']['image'] = 'changed'
            return data
        self.mod.compose_config.side_effect = unsafe
        with self.assertRaisesRegex(self.mod.RecoveryError, '预期之外'):
            self.bind()
        self.assertEqual(self.compose_path.read_bytes(), self.compose_original)
        self.assertFalse(any(call[0] == 'stop' for call in self.calls))

    def test_multi_file_compose_refuses_before_stopping(self):
        self.compose()
        self.fake.lookup('app')['Config']['Labels']['com.docker.compose.project.config_files'] += ',/other/compose.yml'
        with self.assertRaisesRegex(self.mod.RecoveryError, '多文件'):
            self.bind()
        self.assertEqual(self.compose_path.read_bytes(), self.compose_original)
        self.assertFalse(any(call[0] == 'stop' for call in self.calls))

    def test_compose_external_change_is_not_overwritten_on_recovery(self):
        self.compose()
        self.bind()
        self.compose_path.write_text('user edited this file\n')
        with self.assertRaises(self.mod.RecoveryError):
            self.mod.recover_container(self.journal())
        self.assertEqual(self.compose_path.read_text(), 'user edited this file\n')
        self.assertEqual(json.loads(self.journal().read_text())['state'], 'recovery_incomplete')

    def test_compose_interruption_after_file_write_restores_original(self):
        self.compose()
        self.mod.fault = lambda point: (_ for _ in ()).throw(self.mod.Interrupted('TERM')) if point == 'after_compose_binding_write' else None
        with self.assertRaises(self.mod.Interrupted):
            self.bind()
        self.assertEqual(self.compose_path.read_bytes(), self.compose_original)
        self.assertEqual(self.fake.lookup('app')['Id'], self.old['Id'])
