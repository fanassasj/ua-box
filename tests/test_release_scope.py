"""Release boundary and real notification plumbing; no Docker/production operations."""
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

import test_regressions as base
from test_doctor_notifications import health_module


class ReleaseScopeTests(unittest.TestCase):
    def test_all_59_docker_panel_and_migration_functions_match_v064(self):
        fixture = json.loads((base.ROOT / 'tests/fixtures/release_v064_function_hashes.json').read_text())
        self.assertTrue(fixture['baseline'].startswith('af8fe36'))
        self.assertEqual(len(fixture['functions']), 59)
        for name, expected in fixture['functions'].items():
            with self.subTest(function=name):
                body = base.shell_function(name)
                if name in fixture['normalization']['functions']:
                    self.assertEqual(body.count(fixture['normalization']['release_expression']), 1)
                    body = body.replace(fixture['normalization']['release_expression'],
                                        fixture['normalization']['baseline_expression'])
                actual = hashlib.sha256(body.encode()).hexdigest()
                self.assertEqual(actual, expected)

    def test_complete_docker_and_migration_sections_match_baseline(self):
        fixture = json.loads((base.ROOT / 'tests/fixtures/release_v064_function_hashes.json').read_text())
        for begin, record in fixture['sections'].items():
            with self.subTest(section=begin):
                text = base.SCRIPT[base.SCRIPT.index(begin + '() {'):base.SCRIPT.index(record['end'] + '() {')]
                if begin == 'docker_container_exists':
                    self.assertEqual(text.count(fixture['normalization']['release_expression']), 2)
                    text = text.replace(fixture['normalization']['release_expression'],
                                        fixture['normalization']['baseline_expression'])
                self.assertEqual(hashlib.sha256(text.encode()).hexdigest(), record['sha256'])

    def test_legacy_variable_braces_preserve_actual_bash_expansion(self):
        fixture = json.loads((base.ROOT / 'tests/fixtures/release_v064_function_hashes.json').read_text())
        before, after = (fixture['normalization'][key] for key in ('baseline_expression', 'release_expression'))
        for name in ('openlist', 'my-app_2.0', 'nginx-proxy-manager'):
            outputs = []
            for expression in (before, after):
                result = subprocess.run(['bash', '-c', 'container_name="$NAME"; printf "%s" "' + expression + '"'],
                                        env=dict(os.environ, NAME=name), text=True, capture_output=True, timeout=5)
                self.assertEqual(result.returncode, 0, result.stderr)
                outputs.append(result.stdout)
            self.assertEqual(*outputs)

    def test_new_recovery_engine_routes_and_imports_are_absent(self):
        names = set(re.findall(r'^(\w+)\(\) \{', base.SCRIPT, re.M))
        self.assertTrue(names.isdisjoint({'ua_recovery_program', 'run_recovery_operation', 'ua_recover',
                                         'ua_verify_backup', 'restore_migration_archive', 'docker_select_container'}))
        self.assertNotIn("<<'PY_UA_RECOVERY'", base.SCRIPT)
        self.assertNotIn('run_recovery_operation', base.SCRIPT)
        self.assertNotRegex(base.SCRIPT, re.compile(r'^  (recover|verify-backup)\)', re.M))
        self.assertNotIn('ua recover ', base.shell_function('show_help'))
        self.assertNotIn('ua verify-backup ', base.shell_function('show_help'))
        code = base.SCRIPT.split("<<'PY_UA_HEALTH'\n", 1)[1].split('\nPY_UA_HEALTH', 1)[0]
        tree = ast.parse(code)
        self.assertFalse({'update_container', 'recover_container', 'restore_archive', 'configure_daemon'} &
                         {node.name for node in tree.body if isinstance(node, ast.FunctionDef)})
        self.assertNotIn('local_docker_recovery', code)
        self.assertNotIn('compose_port_edit', code)

    def test_version_and_documentation_match_release_scope(self):
        self.assertIn('UA_BOX_VERSION="0.6.5"', base.SCRIPT)
        readme = (base.ROOT / 'README.md').read_text()
        self.assertIn('当前版本：**v0.6.5**', readme)
        self.assertIn('### v0.6.5', readme)
        self.assertIn('暂缓发布', readme)
        self.assertNotIn('共用一个持久化恢复引擎', readme)
        for deferred_claim in ('切换端口绑定沿用原镜像 ID', 'Docker 日志、IPv6 和手动配置编辑统一',
                               'File Browser 默认管理独立目录', 'Fail2ban 仅更新 `[sshd]` 配置段'):
            self.assertNotIn(deferred_claim, readme)
        self.assertFalse((base.ROOT / 'tests/fixtures/filebrowser.bbolt').exists())
        workflow = (base.ROOT / '.github/workflows/checks.yml').read_text()
        self.assertIn('python3 -m unittest discover -s tests -v', workflow)
        self.assertNotIn('go install', workflow)


class NotificationIndependentTests(unittest.TestCase):
    def setUp(self):
        self.mod = health_module()

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

    def run_sender(self, work, kind, reply, status='200'):
        names = ['ua_send_notification', 'ua_webhook_kind', 'ua_notification_post', 'ua_health_operation', 'ua_health_program']
        code = '\n'.join(base.shell_function(name) for name in names) + r'''
has_cmd() { command -v "$1" >/dev/null 2>&1; }
hostname() { echo fixture-host; }
log_action() { :; }
curl() {
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --output) printf '%s' "$REPLY" > "$2"; shift 2 ;;
      -d) printf '%s' "$2" > "$PAYLOAD"; shift 2 ;;
      *) shift ;;
    esac
  done
  printf '%s' "$STATUS"
}
ua_send_notification test-title 'test-body "quoted"'
'''
        env = dict(os.environ, UA_BOX_TMP_DIR=str(work), PAYLOAD=str(work / 'payload.json'), REPLY=reply, STATUS=status,
                   UA_NOTIFY_TG_BOT_TOKEN='fixture-token' if kind == 'telegram' else '',
                   UA_NOTIFY_TG_CHAT_ID='fixture-chat' if kind == 'telegram' else '',
                   UA_NOTIFY_WEBHOOK_URL='' if kind == 'telegram' else 'https://fixture.invalid/',
                   UA_NOTIFY_WEBHOOK_TYPE=kind if kind != 'telegram' else 'auto')
        return subprocess.run(['bash', '-c', code], env=env, text=True, capture_output=True, timeout=10)

    def test_every_platform_uses_real_health_cli_payload_and_ack_without_recovery_engine(self):
        cases = [('telegram', '{"ok":true}', '200'), ('wecom', '{"errcode":0}', '200'),
                 ('feishu', '{"code":0}', '200'), ('dingtalk', '{"errcode":0}', '200'),
                 ('discord', '', '204'), ('bark', '{"code":200}', '200')]
        for kind, reply, status in cases:
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                work = Path(directory)
                keep = work / 'keep'; keep.write_text('user data')
                result = self.run_sender(work, kind, reply, status)
                self.assertEqual(result.returncode, 0, result.stderr)
                payload = json.loads((work / 'payload.json').read_text())
                if kind == 'telegram':
                    self.assertEqual(payload['chat_id'], 'fixture-chat')
                    self.assertIn('test-body "quoted"', payload['text'])
                else:
                    # The same platform-specific function must feed the real shell sender.
                    self.assertIn('test-body "quoted"', json.dumps(payload, ensure_ascii=False).replace('\\"', '"'))
                self.assertFalse(list(work.glob('notify.*')))
                self.assertEqual(keep.read_text(), 'user data')

    def test_http_and_platform_failures_are_never_acknowledged(self):
        for kind, reply, status in [('wecom', '{"errcode":1}', '200'), ('telegram', '{"ok":false}', '200'),
                                    ('discord', '', '500'), ('feishu', 'not-json', '200')]:
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                result = self.run_sender(Path(directory), kind, reply, status)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('已确认接收', result.stdout)
                self.assertFalse(list(Path(directory).glob('notify.*')))


if __name__ == '__main__':
    unittest.main()
