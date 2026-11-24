"""Run with: python3 -m unittest discover -s tests -v (no real Docker/system writes)."""
import io
import hashlib
import sqlite3
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import types
import unittest
from unittest import mock
from fakes import FakeDocker

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / 'ua-box').read_text()


def shell_function(name):
    start = SCRIPT.index(name + '() {')
    return SCRIPT[start:SCRIPT.index('\n}\n', start) + 3]


def embedded_module(marker):
    code = SCRIPT.split("<<'PY_UA_RECOVERY'\n", 1)[1].split("\nPY_UA_RECOVERY", 1)[0]
    module = types.ModuleType(marker)
    exec(compile(code, marker, 'exec'), module.__dict__)
    return module


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


class MigrationTests(unittest.TestCase):
    def setUp(self):
        previous_umask = os.umask(0o077)
        self.addCleanup(os.umask, previous_umask)
        self.mod = embedded_module('PY_MIGRATION_RESTORE')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'target'
        self.root.mkdir()
        self.backup = Path(self.tmp.name) / 'backup'
        self.archive = Path(self.tmp.name) / 'bundle.tar.gz'
        self.config = '/root/ua-box/config'
        self.rclone = '/root/.config/rclone/rclone.conf'

    def bundle(self, entries):
        # Valid archives include an explicit complete-root manifest. Malformed members are retained
        # so the original traversal/link/duplicate rejection assertions still exercise the parser.
        rows = []
        roots = set()
        for name, data, kind in entries:
            owner = next((tree for tree in self.mod.TREES if name == tree or name.startswith(tree + '/')), name)
            roots.add(owner)
            if owner != name:
                parts = Path(name).parts
                for length in range(len(Path(owner).parts), len(parts)):
                    parent = '/'.join(parts[:length])
                    if not any(row[0] == parent for row in rows):
                        rows.append((parent, '', tarfile.DIRTYPE))
            rows.append((name, data, kind))
        manifest = {'format': 2, 'roots': sorted(roots), 'entries': {},
                    'databases': {name: {} for name in roots}}
        for name, data, kind in rows:
            record = {'type': 'dir' if kind == tarfile.DIRTYPE else 'file',
                      'mode': 0o750 if kind == tarfile.DIRTYPE else 0o640,
                      'uid': os.getuid(), 'gid': os.getgid()}
            if kind == tarfile.REGTYPE:
                record.update(size=len(data.encode()), sha256=hashlib.sha256(data.encode()).hexdigest())
            manifest['entries'][name] = record
        with tarfile.open(self.archive, 'w:gz') as tar:
            encoded = json.dumps(manifest).encode()
            info = tarfile.TarInfo('UA-MANIFEST.json')
            info.size = len(encoded)
            tar.addfile(info, io.BytesIO(encoded))
            for name, data, kind in rows:
                member = tarfile.TarInfo(name)
                member.mode = 0o750 if kind == tarfile.DIRTYPE else 0o640
                member.uid, member.gid = os.getuid(), os.getgid()
                member.type = kind
                if kind == tarfile.REGTYPE:
                    data = data.encode()
                    member.size = len(data)
                    tar.addfile(member, io.BytesIO(data))
                else:
                    member.linkname = '/etc/passwd'
                    tar.addfile(member)

    def restore(self, apply=True):
        self.mod.restore_archive(str(self.archive), apply, self.config, self.rclone, str(self.backup), root=self.root, guard=lambda roots: None)

    def test_rejects_paths_links_duplicates_and_special_files_before_writing(self):
        for name, kind in [('etc/passwd', tarfile.REGTYPE), ('/root/caddy/data', tarfile.REGTYPE),
                           ('root/caddy/../../etc/passwd', tarfile.REGTYPE),
                           ('root/caddy/link', tarfile.SYMTYPE), ('root/caddy/link', tarfile.LNKTYPE),
                           ('root/caddy/pipe', tarfile.FIFOTYPE)]:
            with self.subTest(name=name, kind=kind):
                self.bundle([('root/caddy/good', 'good', tarfile.REGTYPE), (name, 'bad', kind)])
                with self.assertRaises(self.mod.RecoveryError):
                    self.restore()
                self.assertFalse((self.root / 'root/caddy/good').exists())
        self.bundle([('root/caddy/data', 'a', tarfile.REGTYPE)] * 2)
        with self.assertRaises(self.mod.RecoveryError):
            self.restore()

    def test_rejects_target_symlink_and_config_commands(self):
        outside = Path(self.tmp.name) / 'outside'
        outside.mkdir()
        (self.root / 'root').mkdir()
        (self.root / 'root/caddy').symlink_to(outside, target_is_directory=True)
        self.bundle([('root/caddy/data', 'bad', tarfile.REGTYPE)])
        with self.assertRaises(self.mod.RecoveryError):
            self.restore()
        self.assertEqual(list(outside.iterdir()), [])
        (self.root / 'root/caddy').unlink()
        self.bundle([('root/ua-box/config', 'UA_NOTIFY_WEBHOOK_URL=x; touch /tmp/unsafe', tarfile.REGTYPE)])
        with self.assertRaises(self.mod.RecoveryError):
            self.restore()

    def test_restore_and_preoverwrite_backup_and_config_literal(self):
        target = self.root / 'root/caddy/Caddyfile'
        target.parent.mkdir(parents=True)
        target.write_text('before')
        self.bundle([('root/caddy/Caddyfile', 'after', tarfile.REGTYPE),
                     ('root/ua-box/config', 'UA_NOTIFY_WEBHOOK_URL="$(printf EXECUTED)"', tarfile.REGTYPE)])
        self.restore(False)
        self.assertEqual(target.read_text(), 'before')
        self.restore()
        self.assertEqual(target.read_text(), 'after')
        journal = json.loads(next(self.backup.glob('pre-import-*/journal.json')).read_text())
        original = Path(next(e['slot'] for e in journal['entries'] if e['target'].endswith('/root/caddy')))
        self.assertEqual((original / 'Caddyfile').read_text(), 'before')
        cfg = self.root / 'root/ua-box/config'
        p = subprocess.run(['bash', '-c', '. "$1"; printf "%s" "$UA_NOTIFY_WEBHOOK_URL"', 'bash', str(cfg)],
                           capture_output=True, text=True)
        self.assertEqual(p.stdout, '$(printf EXECUTED)')
        self.assertEqual(cfg.stat().st_mode & 0o777, 0o600)
        self.assertEqual(original.parent.stat().st_mode & 0o777, 0o700)

    def test_restores_new_directory_permissions(self):
        self.bundle([('root/caddy/private', '', tarfile.DIRTYPE),
                     ('root/caddy/private/data', 'secret', tarfile.REGTYPE)])
        self.restore()
        self.assertEqual((self.root / 'root/caddy/private').stat().st_mode & 0o777, 0o750)

    def test_write_failure_restores_overwritten_and_removes_new_files(self):
        first = self.root / 'root/caddy/first'
        first.parent.mkdir(parents=True)
        first.write_text('original')
        self.bundle([('root/caddy/first', 'changed', tarfile.REGTYPE),
                     ('root/caddy/new', 'new', tarfile.REGTYPE),
                     ('root/caddy/last', 'last', tarfile.REGTYPE)])
        copy2 = self.mod.shutil.copy2
        def fail_last(src, dst, *args, **kwargs):
            if str(dst).endswith('/last'):
                raise OSError('simulated disk full')
            return copy2(src, dst, *args, **kwargs)
        with mock.patch.object(self.mod.shutil, 'copy2', side_effect=fail_last):
            with self.assertRaises(OSError):
                self.restore()
        self.assertEqual(first.read_text(), 'original')
        self.assertFalse((first.parent / 'new').exists())
        self.assertFalse((first.parent / 'last').exists())


class ContainerTests(unittest.TestCase):
    def setUp(self):
        previous_umask = os.umask(0o077)
        self.addCleanup(os.umask, previous_umask)
        self.mod = embedded_module('PY_CONTAINER_UPDATE')
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name) / 'app-data'
        self.data.mkdir()
        with sqlite3.connect(self.data / 'data.db') as database:
            database.execute('CREATE TABLE sample(value TEXT)')
            database.execute("INSERT INTO sample VALUES ('before')")
        self.old = {
            'Id': 'a' * 64, 'Image': 'old:image', 'Name': '/app', 'State': {'Running': True, 'Status': 'running'},
            'Config': {'Image': 'app:latest', 'Env': ['CUSTOM=keep'], 'Labels': {'custom': 'keep'},
                       'Cmd': ['custom-command'], 'User': '1000'},
            'HostConfig': {'NetworkMode': 'private', 'RestartPolicy': {'Name': 'unless-stopped'},
                           'Binds': ['/srv/custom:/data:ro'], 'Memory': 123456,
                           'PortBindings': {'80/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '8888'}]}},
            'Mounts': [{'Type': 'volume', 'Name': 'anonymous123', 'Destination': '/database',
                        'Source': str(self.data), 'RW': True}],
            'NetworkSettings': {'Networks': {'private': {'Aliases': ['app', 'a' * 12],
                'IPAddress': '172.18.0.2', 'EndpointID': 'runtime', 'IPAMConfig': {'IPv4Address': '172.18.0.2'}}}},
        }
        self.fake = FakeDocker(self.old)
        self.calls, self.request_calls = self.fake.calls, self.fake.requests
        self.mod.docker = self.fake
        self.mod.Engine = self.fake.engine()
        self.real_offline_guard = self.mod.offline_guard
        self.mod.offline_guard = lambda roots: None
        self.mod.wait_ready = mock.Mock()
        self.mod.signal = mock.Mock()

    def update(self):
        return self.mod.update_container('app', self.tmp.name, '5')

    def test_create_preserves_settings_and_reuses_anonymous_volumes(self):
        config = self.mod.create_config(self.old, 'new:image')
        self.assertEqual(config['Env'], ['CUSTOM=keep'])
        self.assertEqual(config['Cmd'], ['custom-command'])
        self.assertEqual(config['HostConfig']['PortBindings'], self.old['HostConfig']['PortBindings'])
        self.assertEqual(config['HostConfig']['Memory'], 123456)
        self.assertIn('anonymous123:/database:rw', config['HostConfig']['Binds'])
        endpoint = config['NetworkingConfig']['EndpointsConfig']['private']
        self.assertEqual(endpoint['IPAMConfig'], {'IPv4Address': '172.18.0.2'})
        self.assertNotIn('IPAddress', endpoint)
        self.assertEqual(endpoint['Aliases'], ['app'])
        self.assertEqual(self.old['Config']['Image'], 'app:latest')

    def test_success_retains_old_and_original_data_after_readiness(self):
        self.mod.wait_ready.side_effect = lambda *args: self.calls.append(('ready',))
        self.update()
        self.assertIn(('ready',), self.calls)
        self.assertFalse(any(c[0] == 'rm' for c in self.calls))
        self.assertIn(self.old['Id'], self.fake.containers)
        self.assertFalse(self.fake.lookup(self.old['Id'])['State']['Running'])
        self.assertNotIn(('image', 'prune', '-f'), self.calls)
        snapshots = list(Path(self.tmp.name).glob('container-*.json'))
        self.assertEqual(snapshots[0].stat().st_mode & 0o777, 0o600)

    def test_create_failure_restores_network_and_old_container(self):
        self.fake.fail_create = True
        with self.assertRaises(RuntimeError):
            self.update()
        self.assertIn(('start', self.old['Id']), self.calls)
        self.assertTrue(any(path.endswith('/connect') for _, path, _ in self.request_calls))
        self.assertNotIn(('rm', self.old['Id']), self.calls)

    def test_start_and_health_failures_retain_candidate_and_restore_old(self):
        for failure in ['start', 'health']:
            with self.subTest(failure=failure):
                self.calls.clear()
                self.fake.fail_start = failure == 'start'
                self.mod.wait_ready.side_effect = (lambda identifier, timeout: (_ for _ in ()).throw(RuntimeError('unhealthy')) if identifier in self.fake.new_ids else None) if failure == 'health' else None
                with self.assertRaises(Exception):
                    self.update()
                self.assertFalse(any(c[0] == 'rm' for c in self.calls))
                self.assertFalse(self.fake.lookup(self.fake.new_ids[-1])['State']['Running'])
                self.assertIn(('start', self.old['Id']), self.calls)
                self.assertEqual(json.loads((Path(self.tmp.name) / '.container-update-app.json').read_text())['state'], 'recovered')
                self.assertNotIn(('rm', self.old['Id']), self.calls)

    def test_main_reports_failure(self):
        self.mod.update_container = mock.Mock(side_effect=RuntimeError('failed'))
        with mock.patch.object(self.mod.sys, 'argv', ['-', 'update', 'app', self.tmp.name, '5']):
            self.assertEqual(self.mod.main(), 1)

    def test_wait_ready_rejects_stopped_unhealthy_and_timeout(self):
        module = embedded_module('PY_CONTAINER_UPDATE')
        for state in [{'Running': False}, {'Running': True, 'Health': {'Status': 'unhealthy'}}]:
            with self.subTest(state=state):
                module.docker = lambda *args: json.dumps([{'State': state}])
                with self.assertRaises(RuntimeError):
                    module.wait_ready('new', 3)
        module.docker = lambda *args: json.dumps([{'State': {'Running': True, 'Health': {'Status': 'starting'}}}])
        with mock.patch.object(module.time, 'monotonic', side_effect=[0, 1, 10]), mock.patch.object(module.time, 'sleep'):
            with self.assertRaises(RuntimeError):
                module.wait_ready('new', 3)

    def test_rejects_unsafe_container_before_stopping(self):
        self.fake.lookup('app')['HostConfig']['AutoRemove'] = True
        with self.assertRaises(RuntimeError):
            self.update()
        self.assertFalse(any(c[0] == 'stop' for c in self.calls))


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


if __name__ == '__main__':
    unittest.main()
