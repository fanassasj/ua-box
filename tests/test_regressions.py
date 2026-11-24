"""General and installer regressions for the non-Docker v0.6.5 release."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / 'ua-box').read_text()


def shell_function(name):
    start = SCRIPT.index(name + '() {')
    return SCRIPT[start:SCRIPT.index('\n}\n', start) + 3]


class ShellRegressionTests(unittest.TestCase):
    def run_bash(self, code, **kwargs):
        return subprocess.run(['bash'], input=code, text=True, capture_output=True, **kwargs)

    def test_menu_returns_only_selection_and_cancel_is_empty(self):
        function = shell_function('select_with_fzf_or_menu')
        for choice, expected, success in [('1', 'alpha', True), ('0', '', False), ('99', '', False)]:
            with self.subTest(choice=choice):
                p = self.run_bash(function + '\nhas_cmd() { return 1; }\n' +
                                  f'select_with_fzf_or_menu "选择" alpha beta <<< {choice}\n')
                self.assertEqual(p.stdout.strip(), expected)
                self.assertEqual(p.returncode == 0, success)
                self.assertIn('alpha', p.stderr)

    def test_config_values_are_literal_and_private(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg = Path(directory) / 'config'
            cfg.write_text('UA_TEST_VALUE="old"\n')
            values = ['literal-$(printf EXECUTED)', '`printf EXECUTED`', "a'b\"c\\d$HOME", '  spaces # = ; | &  ', '中文', '']
            for value in values:
                with self.subTest(value=value):
                    env = dict(os.environ, UA_BOX_CONFIG=str(cfg), TEST_INPUT=value)
                    p = self.run_bash(shell_function('config_set_var') + '\n' +
                                      'config_set_var UA_TEST_VALUE "$TEST_INPUT" || exit $?\nprintf "%s" "$UA_TEST_VALUE"\n', env=env)
                    self.assertEqual(p.returncode, 0, p.stderr)
                    self.assertEqual(p.stdout, value)
                    self.assertEqual(cfg.stat().st_mode & 0o777, 0o600)
            original = cfg.read_bytes()
            p = self.run_bash(shell_function('config_set_var') + '\nconfig_set_var UA_TEST_VALUE $\'one\\ntwo\'\n',
                              env=dict(os.environ, UA_BOX_CONFIG=str(cfg)))
            self.assertNotEqual(p.returncode, 0)
            self.assertEqual(cfg.read_bytes(), original)

    def test_full_help_uses_isolated_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env = dict(os.environ, UA_BOX_CONFIG=str(root / 'config'), UA_BOX_LOG_DIR=str(root / 'logs'),
                       UA_BOX_BACKUP_DIR=str(root / 'backups'))
            p = subprocess.run(['bash', str(ROOT / 'ua-box'), '--help'], env=env, capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn('UA Box', p.stdout)
            self.assertEqual((root / 'config').stat().st_mode & 0o777, 0o600)

    def test_compose_state_uses_ps_state_field(self):
        p = self.run_bash(shell_function('get_compose_projects_summary') + r'''
has_cmd() { return 0; }
docker() {
  [ "$1" = info ] && return 0
  case "$*" in *State.Running*) return 1;; esac
  printf 'web\tapp\trunning\ndb\tapp\texited\nother\tfull\trunning\n'
}
get_compose_projects_summary
''')
        self.assertEqual(p.returncode, 0)
        self.assertIn('app (1/2 部分停止)', p.stdout)
        self.assertIn('full (1/1 运行)', p.stdout)


class InstallTests(unittest.TestCase):
    def test_download_validation_and_activation_failures_preserve_install(self):
        for failure in ['download', 'syntax', 'activation', 'success']:
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                install = root / 'install'
                install.mkdir()
                original = {'ua-box': '#!/bin/bash\necho previous\n', 'README.md': 'previous readme',
                            '.gitignore': 'previous ignore', 'config': 'KEEP="secret"\n'}
                for name, data in original.items():
                    (install / name).write_text(data)
                (install / 'ua-box').chmod(0o755)
                source = root / 'source'
                source.mkdir()
                new_script = '#!/bin/bash\nUA_BOX_VERSION="test"\ncreate_local_links() { :; }\n'
                new_script += ('exit 1\n' if failure == 'activation' else 'exit 0\n')
                (source / 'ua-box').write_text('if\n' if failure == 'syntax' else new_script)
                (source / 'README.md').write_text('new readme')
                (source / '.gitignore').write_text('new ignore')
                bins = root / 'bin'
                bins.mkdir()
                (bins / 'id').write_text('#!/bin/bash\necho 0\n')
                (bins / 'curl').write_text('''#!/bin/bash
url="${@: -3:1}"
out="${@: -1}"
name="${url##*/}"
if [ "$TEST_FAILURE" = download ] && [ "$name" = README.md ]; then
  printf partial > "$out"
  exit 22
fi
cp "$TEST_SOURCE/$name" "$out"
''')
                for f in bins.iterdir():
                    f.chmod(0o755)
                env = dict(os.environ, PATH=str(bins) + ':' + os.environ['PATH'],
                           UA_BOX_INSTALL_DIR=str(install), TEST_SOURCE=str(source), TEST_FAILURE=failure)
                p = subprocess.run(['bash', str(ROOT / 'install.sh')], env=env, capture_output=True, text=True)
                self.assertEqual(p.returncode == 0, failure == 'success', p.stderr)
                for name, data in original.items():
                    if failure != 'success' or name == 'config':
                        self.assertEqual((install / name).read_text(), data)
                self.assertEqual(list(install.glob('.ua-install.*')), [])
