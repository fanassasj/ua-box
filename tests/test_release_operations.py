"""Read-only inventory and private temporary-directory regressions, with fake services."""
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import unittest

import test_regressions as base


class ReadOnlyDailyTests(unittest.TestCase):
    def bash(self, functions, code, stdin='', env=None):
            source = '\n'.join(base.shell_function(f) for f in functions)
            return subprocess.run(['bash', '-c', source + '\n' + code], input=stdin,
                                  text=True, capture_output=True, timeout=10, env=env)

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

    def test_main_menu_eof_exits_without_an_operation(self):
        result = self.bash(['main_menu'], 'clear() { :; }; main_menu')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count('请输入'), 0)
