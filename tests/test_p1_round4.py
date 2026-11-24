"""Two P1 fixes only. Fake daemon tests never connect to Docker; all DBs are temporary."""
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import signal
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

import test_regressions as base


class RestartPolicyP1(unittest.TestCase):
    def setUp(self):
        base.ContainerTests.setUp(self)
        self.wanted = {'Name': 'always', 'MaximumRetryCount': 0}
        self.fake.lookup(self.old['Id'])['HostConfig']['RestartPolicy'] = dict(self.wanted)

    def update(self):
        return self.mod.update_container('app', self.tmp.name, '5')

    def journal(self):
        return Path(self.tmp.name) / '.container-update-app.json'

    def assert_single_formal(self, identifier):
        self.fake.advance(12)
        self.assertEqual(self.fake.daemon_restart(), {identifier})
        self.assertEqual(self.fake.resource_conflicts(), [])

    def test_success_isolates_old_always_and_daemon_restarts_only_formal(self):
        self.update()
        formal = self.fake.new_ids[-1]
        self.assertEqual(self.fake.lookup(self.old['Id'])['HostConfig']['RestartPolicy'], self.mod.NO_RESTART)
        self.assertEqual(self.fake.lookup(formal)['HostConfig']['RestartPolicy'], self.wanted)
        record = json.loads(self.journal().read_text())
        self.assertEqual(record['restart_policies']['old_original'], self.wanted)
        self.assertEqual(record['restart_policies']['candidate_initial'], self.mod.NO_RESTART)
        self.assertEqual(record['restart_policies']['candidate_requested'], self.wanted)
        self.assert_single_formal(formal)

    def test_failed_promoted_always_candidate_is_recorded_and_isolated(self):
        def fail_promoted(point):
            if point == 'after_candidate_promotion':
                raise RuntimeError('failed after promotion')
        self.mod.fault = fail_promoted
        with self.assertRaises(RuntimeError):
            self.update()
        candidate = self.fake.new_ids[-1]
        record = json.loads(self.journal().read_text())
        self.assertEqual(record['restart_policies']['retained_original'][candidate], self.wanted)
        self.assertEqual(self.fake.lookup(candidate)['HostConfig']['RestartPolicy'], self.mod.NO_RESTART)
        self.assertFalse(self.fake.lookup(candidate)['State']['Running'])
        self.assertEqual(record['state'], 'recovered')
        self.assert_single_formal(self.old['Id'])

    def test_failed_initial_candidate_never_auto_restarts(self):
        self.fake.fail_start = True
        with self.assertRaises(subprocess.CalledProcessError):
            self.update()
        candidate = self.fake.new_ids[-1]
        self.assertEqual(self.fake.lookup(candidate)['HostConfig']['RestartPolicy'], self.mod.NO_RESTART)
        self.assert_single_formal(self.old['Id'])

    def test_repeated_rollback_keeps_only_selected_original_policy(self):
        self.update()
        self.mod.recover_container(self.journal())
        self.mod.recover_container(self.journal())
        self.assertEqual(self.fake.lookup(self.old['Id'])['HostConfig']['RestartPolicy'], self.wanted)
        self.assertEqual(self.fake.lookup(self.fake.new_ids[-1])['HostConfig']['RestartPolicy'], self.mod.NO_RESTART)
        self.assert_single_formal(self.old['Id'])

    def test_old_policy_restore_failure_is_not_reported_as_success_and_can_retry(self):
        self.fake.fail_create = True
        self.fake.fail_policy = lambda identifier, policy: identifier == self.old['Id'] and policy['Name'] == 'always'
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(RuntimeError):
            self.update()
        record = json.loads(self.journal().read_text())
        self.assertEqual(record['state'], 'recovery_incomplete')
        self.assertEqual(record['observed']['restart_policy'], self.mod.NO_RESTART)
        self.assertTrue(record['observed']['state']['Running'])
        self.assertTrue(any(e['step'] == 'restore_old_restart' for e in record['errors']))
        self.assertNotIn('已验证旧容器', output.getvalue())
        self.fake.fail_policy = None
        self.mod.recover_container(self.journal())
        self.assertEqual(json.loads(self.journal().read_text())['state'], 'recovered')
        self.assert_single_formal(self.old['Id'])

    def test_old_isolation_failure_does_not_stop_or_replace_original(self):
        self.fake.fail_policy = lambda identifier, policy: identifier == self.old['Id'] and policy['Name'] == 'no'
        before = self.mod.inventory(self.data)
        with self.assertRaises(self.mod.RecoveryError):
            self.update()
        self.assertTrue(self.fake.lookup(self.old['Id'])['State']['Running'])
        self.assertFalse(self.fake.new_ids)
        self.assertFalse(any(call[0] == 'stop' for call in self.calls))
        self.assertEqual(self.mod.inventory(self.data), before)

    def test_candidate_isolation_failure_blocks_data_rollback_and_old_start(self):
        def fail_after_promotion(point):
            if point == 'after_candidate_promotion':
                self.fake.fail_policy = lambda identifier, policy: identifier in self.fake.new_ids and policy['Name'] == 'no'
                raise RuntimeError('isolation update unavailable')
        self.mod.fault = fail_after_promotion
        with self.assertRaises(RuntimeError):
            self.update()
        record = json.loads(self.journal().read_text())
        self.assertEqual(record['state'], 'recovery_incomplete')
        self.assertFalse(record['observed']['data_restored'])
        self.assertFalse(self.fake.lookup(self.old['Id'])['State']['Running'])
        self.assertNotIn(('start', self.old['Id']), self.calls)
        self.assertEqual(record['observed']['retained_candidate']['restart_policy'], self.wanted)
        self.assertLessEqual(len(self.fake.daemon_restart()), 1)
        self.assertFalse(self.fake.resource_conflicts())

    def test_formal_policy_promotion_failure_rolls_back_instead_of_committing(self):
        def fail_promotion(container):
            self.fake.fail_policy = lambda identifier, policy: identifier in self.fake.new_ids and policy['Name'] == 'always'
        self.fake.after_create = fail_promotion
        with self.assertRaises(self.mod.RecoveryError):
            self.update()
        self.assertEqual(json.loads(self.journal().read_text())['state'], 'recovered')
        self.assert_single_formal(self.old['Id'])

    def test_policy_response_lost_is_verified_from_actual_state(self):
        def lost_response(container, policy):
            raise TimeoutError('response lost after policy applied')
        self.fake.after_policy_update = lost_response
        self.update()
        self.assert_single_formal(self.fake.new_ids[-1])

    def test_daemon_restart_during_old_isolation_and_candidate_creation_has_no_conflicts(self):
        self.fake.after_policy_update = lambda container, policy: self.fake.daemon_restart()
        self.fake.after_create = lambda container: self.fake.daemon_restart()
        self.update()
        self.assertTrue(all(not event['conflicts'] for event in self.fake.daemon_events))
        self.assert_single_formal(self.fake.new_ids[-1])

    def test_api_success_without_actual_policy_change_is_not_accepted(self):
        def ignore_applied_policy(container, policy):
            container['HostConfig']['RestartPolicy'] = dict(self.wanted)
        self.fake.after_policy_update = ignore_applied_policy
        with self.assertRaises(self.mod.RecoveryError):
            self.mod.set_restart_policy(self.mod.Engine(), self.old['Id'], self.mod.NO_RESTART)
        self.assertEqual(self.fake.lookup(self.old['Id'])['HostConfig']['RestartPolicy'], self.wanted)

    def test_on_failure_retry_count_is_preserved_on_restore(self):
        wanted = {'Name': 'on-failure', 'MaximumRetryCount': 7}
        self.fake.lookup(self.old['Id'])['HostConfig']['RestartPolicy'] = wanted
        self.fake.fail_create = True
        with self.assertRaises(RuntimeError):
            self.update()
        self.assertEqual(self.fake.lookup(self.old['Id'])['HostConfig']['RestartPolicy'], wanted)
        self.assertEqual(self.fake.daemon_restart(), set())

    def test_simulator_models_manual_stop_difference_and_exposes_unisolated_conflicts(self):
        self.fake('stop', self.old['Id'])
        self.assertEqual(self.fake.daemon_restart(), {self.old['Id']})
        self.fake.lookup(self.old['Id'])['HostConfig']['RestartPolicy']['Name'] = 'unless-stopped'
        self.fake('stop', self.old['Id'])
        self.assertEqual(self.fake.daemon_restart(), set())
        other = copy.deepcopy(self.fake.lookup(self.old['Id']))
        other['Id'], other['Name'] = 'other', '/other'
        other['HostConfig']['RestartPolicy'] = dict(self.wanted)
        self.fake.containers['other'] = other
        self.fake.armed.add('other')
        self.fake.lookup(self.old['Id'])['HostConfig']['RestartPolicy'] = dict(self.wanted)
        self.assertEqual(self.fake.daemon_restart(), {self.old['Id'], 'other'})
        self.assertTrue(any(c[0] == 'volume' for c in self.fake.resource_conflicts()))
        self.assertTrue(any(c[0] == 'port' for c in self.fake.resource_conflicts()))


class NpmBackendP1(unittest.TestCase):
    def setUp(self):
        base.ContainerTests.setUp(self)
        self.npm = Path(self.tmp.name) / 'npm'
        self.npm_data = self.npm / 'data'
        self.npm_data.mkdir(parents=True)
        with sqlite3.connect(self.npm_data / 'database.sqlite') as db:
            db.execute('CREATE TABLE sample(value TEXT)')
            db.execute("INSERT INTO sample VALUES ('stale-or-current')")
        self.runtime = self.fake.lookup(self.old['Id'])
        self.runtime['Config'].update(Image='jc21/nginx-proxy-manager:2.12.6', WorkingDir='/app', Env=['NODE_ENV=production'], Entrypoint=['/init'], Cmd=None)
        self.runtime['Mounts'][0].update(Source=str(self.npm_data), Destination='/data')
        self.file_config = None
        self.mod.npm_config_file = lambda runtime, environment: self.file_config
        original_which = shutil.which
        patch = mock.patch.object(self.mod.shutil, 'which', side_effect=lambda name: '/fake/docker' if name == 'docker' else original_which(name))
        patch.start(); self.addCleanup(patch.stop)
        self.archive = Path(self.tmp.name) / 'npm.tar.gz'
        self.secret = 'DO-NOT-LOG-password://user:private@host'

    def mysql(self):
        self.runtime['Config']['Env'] = ['DB_MYSQL_HOST=db', 'DB_MYSQL_USER=npm', 'DB_MYSQL_NAME=npm', 'DB_MYSQL_PASSWORD=' + self.secret]

    def export(self):
        return self.mod.build_archive([('root/nginx-proxy-manager', self.npm)], self.archive, guard=lambda roots: None)

    def update(self):
        return self.mod.update_container('app', self.tmp.name, '5')

    def test_sqlite_backend_comes_from_effective_runtime_not_filename(self):
        decision = self.mod.identify_npm_backend(self.runtime)
        self.assertEqual(decision['backend'], 'sqlite')
        self.assertEqual(decision['sqlite_file'], '/data/database.sqlite')
        self.assertEqual(decision['source'], 'runtime-env')
        self.assertEqual(self.mod.database_expectations(self.runtime, [self.npm_data]), [self.npm_data / 'database.sqlite'])

    def test_mysql_and_postgres_are_identified(self):
        self.mysql()
        self.assertEqual(self.mod.identify_npm_backend(self.runtime)['backend'], 'mysql')
        self.runtime['Config']['Env'] = ['DB_POSTGRES_HOST=db', 'DB_POSTGRES_USER=npm', 'DB_POSTGRES_NAME=npm']
        self.assertEqual(self.mod.identify_npm_backend(self.runtime)['backend'], 'postgres')

    def test_external_backend_with_residual_sqlite_is_rejected_by_export_and_update(self):
        self.mysql()
        before = self.mod.inventory(self.npm)
        output = io.StringIO()
        for operation in (self.update, self.export):
            with self.subTest(operation=operation.__name__), contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
                with self.assertRaises(self.mod.RecoveryError) as error:
                    operation()
                self.assertNotIn(self.secret, str(error.exception))
        self.assertNotIn(self.secret, output.getvalue())
        self.assertFalse(self.archive.exists())
        self.assertFalse(any(call[0] == 'stop' for call in self.calls))
        self.assertEqual(self.mod.inventory(self.npm), before)

    def test_partial_external_selectors_are_unknown_and_rejected_consistently(self):
        for environment in [['DB_MYSQL_HOST=db'], ['DB_MYSQL_PASSWORD=' + self.secret], ['DB_POSTGRES_USER=npm'], ['DB_MYSQL_HOST__FILE=/run/secrets/host']]:
            with self.subTest(environment=environment[0].split('=')[0]):
                self.runtime['Config']['Env'] = environment
                self.assertEqual(self.mod.identify_npm_backend(self.runtime)['backend'], 'unknown')
                for operation in (self.update, self.export):
                    with self.assertRaises(self.mod.RecoveryError):
                        operation()
        self.assertFalse(self.archive.exists())

    def test_unrecognizable_runtime_and_config_do_not_fall_back_to_sqlite(self):
        for environment, config in [(None, None), (['broken'], None), (['X=1', 'X=2'], None), ([], {'database': {}}), ([], {'database': {'engine': self.secret}})]:
            with self.subTest(environment=environment):
                self.runtime['Config']['Env'], self.file_config = environment, config
                self.assertEqual(self.mod.identify_npm_backend(self.runtime)['backend'], 'unknown')
                with self.assertRaises(self.mod.RecoveryError) as error:
                    self.export()
                self.assertNotIn(self.secret, str(error.exception))

    def test_custom_startup_cannot_hide_external_database_selectors(self):
        self.runtime['Config']['Cmd'] = ['sh', '-c', 'export DB_MYSQL_HOST=hidden; /init']
        self.assertEqual(self.mod.identify_npm_backend(self.runtime)['backend'], 'unknown')
        for operation in (self.update, self.export):
            with self.assertRaises(self.mod.RecoveryError):
                operation()
        self.assertFalse(self.archive.exists())

    def test_file_configuration_has_precedence_over_external_environment(self):
        self.mysql()
        self.file_config = {'database': {'engine': 'knex-native', 'knex': {'client': 'sqlite3', 'connection': {'filename': '/data/database.sqlite'}}}}
        self.assertEqual(self.mod.identify_npm_backend(self.runtime)['backend'], 'sqlite')
        manifest = self.export()
        self.assertEqual(manifest['database_backends']['root/nginx-proxy-manager']['source'], 'runtime-file')
        self.update()

    def test_external_file_configuration_overrides_sqlite_environment(self):
        self.file_config = {'database': {'engine': 'mysql2', 'host': 'db', 'password': self.secret}}
        self.assertEqual(self.mod.identify_npm_backend(self.runtime)['backend'], 'mysql')
        with self.assertRaises(self.mod.RecoveryError):
            self.export()
        with self.assertRaises(self.mod.RecoveryError):
            self.update()

    def test_existing_sqlite_not_at_effective_path_is_not_current_database(self):
        self.runtime['Config']['Env'] = ['DB_SQLITE_FILE=/data/actually-used.sqlite']
        self.assertTrue((self.npm_data / 'database.sqlite').exists())
        for operation in (self.update, self.export):
            with self.assertRaises(self.mod.RecoveryError):
                operation()
        self.assertFalse(self.archive.exists())

    def test_custom_actual_sqlite_and_residual_default_are_distinguished(self):
        with sqlite3.connect(self.npm_data / 'actual.sqlite') as db:
            db.execute('CREATE TABLE active(value TEXT)')
        self.runtime['Config']['Env'] = ['DB_SQLITE_FILE=/data/actual.sqlite']
        manifest = self.export()
        self.assertEqual(manifest['database_backends']['root/nginx-proxy-manager']['sqlite_file'], '/data/actual.sqlite')
        self.assertEqual(self.mod.database_expectations(self.runtime, [self.npm_data]), [self.npm_data / 'actual.sqlite'])

    def test_update_export_and_archive_verification_use_same_backend_evidence(self):
        decision = self.mod.identify_npm_backend(self.runtime)
        manifest = self.export()
        self.assertEqual(manifest['database_backends']['root/nginx-proxy-manager'], decision)
        with self.mod.verified_archive(self.archive) as (stage, checked):
            self.assertEqual(checked['database_backends'], manifest['database_backends'])
        self.update()
        record = json.loads((Path(self.tmp.name) / '.container-update-app.json').read_text())
        self.assertEqual(record['database_backend'], decision)
        self.assertTrue(self.fake.lookup('app')['State']['Running'])
        self.assertEqual(self.mod.identify_npm_backend(self.fake.lookup('app'))['backend'], decision['backend'])

    def test_missing_or_ambiguous_runtime_is_rejected_despite_sqlite_file(self):
        runtime = self.fake.containers.pop(self.old['Id'])
        with self.assertRaises(self.mod.RecoveryError):
            self.export()
        self.fake.containers[self.old['Id']] = runtime
        duplicate = copy.deepcopy(runtime)
        duplicate['Id'], duplicate['Name'] = 'duplicate-id', '/duplicate'
        self.fake.containers['duplicate-id'] = duplicate
        with self.assertRaises(self.mod.RecoveryError):
            self.export()

    def test_retained_container_is_not_chosen_as_formal_npm_runtime(self):
        duplicate = copy.deepcopy(self.runtime)
        duplicate['Id'], duplicate['Name'] = 'old-retained', '/app-ua-old-123456abcdef'
        duplicate['Config']['Env'] = ['DB_MYSQL_HOST=db', 'DB_MYSQL_USER=npm', 'DB_MYSQL_NAME=npm']
        self.fake.containers['old-retained'] = duplicate
        self.assertEqual(self.export()['database_backends']['root/nginx-proxy-manager']['backend'], 'sqlite')

    def test_migration_rejects_external_destination_with_stale_sqlite(self):
        self.export()
        root = Path(self.tmp.name) / 'destination'
        target = root / 'root/nginx-proxy-manager'
        shutil.copytree(self.npm, target)
        self.runtime['Mounts'][0]['Source'] = str(target / 'data')
        self.mysql()
        before = self.mod.inventory(target)
        with self.assertRaises(self.mod.RecoveryError):
            self.mod.restore_archive(self.archive, True, '/root/ua-box/config', '/root/.config/rclone/rclone.conf',
                                     Path(self.tmp.name) / 'restore-backup', root=root, guard=lambda roots: None)
        self.assertEqual(self.mod.inventory(target), before)

    def test_sqlite_migration_checks_target_backend_and_restores_actual_database(self):
        self.export()
        root = Path(self.tmp.name) / 'destination'
        target = root / 'root/nginx-proxy-manager'
        shutil.copytree(self.npm, target)
        (target / 'old-extra-file').write_text('retained in original only')
        self.runtime['Mounts'][0]['Source'] = str(target / 'data')
        self.runtime['State']['Running'] = False
        self.mod.restore_archive(self.archive, True, '/root/ua-box/config', '/root/.config/rclone/rclone.conf',
                                 Path(self.tmp.name) / 'restore-backup', root=root, guard=lambda roots: None)
        self.assertFalse((target / 'old-extra-file').exists())
        with sqlite3.connect(target / 'data/database.sqlite') as database:
            self.assertEqual(database.execute('SELECT value FROM sample').fetchall(), [('stale-or-current',)])
            self.assertEqual(database.execute('PRAGMA integrity_check').fetchone(), ('ok',))

    def test_migration_requires_source_backend_evidence_even_for_valid_sqlite(self):
        self.export()
        rows = []
        with tarfile.open(self.archive) as archive:
            for member in archive:
                content = archive.extractfile(member).read() if member.isfile() else None
                if member.name == 'UA-MANIFEST.json':
                    manifest = json.loads(content); manifest.pop('database_backends')
                    content = json.dumps(manifest).encode(); member.size = len(content)
                rows.append((member, content))
        with tarfile.open(self.archive, 'w:gz') as archive:
            for member, content in rows:
                archive.addfile(member, io.BytesIO(content) if content is not None else None)
        with self.assertRaises(self.mod.RecoveryError):
            with self.mod.verified_archive(self.archive):
                self.fail('old SQLite file was accepted without runtime backend evidence')

    def test_real_config_file_reader_suppresses_secrets_and_does_not_exec(self):
        actual_module = base.embedded_module('npm-config-reader')
        config = {'database': {'engine': 'mysql2', 'password': self.secret}}
        raw = json.dumps(config).encode()
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode='w') as archive:
            member = tarfile.TarInfo('production.json'); member.size = len(raw)
            archive.addfile(member, io.BytesIO(raw))
        result = subprocess.CompletedProcess([], 0, stdout=buffer.getvalue(), stderr=b'')
        with mock.patch.object(actual_module.subprocess, 'run', return_value=result) as run:
            self.assertEqual(actual_module.identify_npm_backend(self.runtime)['backend'], 'mysql')
            self.assertEqual(run.call_args.args[0][0:2], ['docker', 'cp'])
        error = subprocess.CompletedProcess([], 1, stdout=b'', stderr=self.secret.encode())
        with mock.patch.object(actual_module.subprocess, 'run', return_value=error):
            self.assertEqual(actual_module.identify_npm_backend(self.runtime)['backend'], 'unknown')
            with self.assertRaises(actual_module.RecoveryError) as raised:
                actual_module.require_npm_sqlite(actual_module.identify_npm_backend(self.runtime))
            self.assertNotIn(self.secret, str(raised.exception))


class RestartPolicySignals(unittest.TestCase):
    def test_sigkill_after_promotion_daemon_restart_then_recovery_reconciles_policies(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'state.json'
            code = """
import sys,json,os,signal
from pathlib import Path
sys.path.insert(0, TESTS_PATH)
import test_regressions as base
case=base.ContainerTests();case.setUp();case.mod.signal=signal
case.fake.lookup(case.old['Id'])['HostConfig']['RestartPolicy']={'Name':'always','MaximumRetryCount':0}
def terminate(point):
 if point=='after_candidate_promotion':
  case.fake.advance(12)
  Path(sys.argv[1]).write_text(json.dumps({'containers':case.fake.containers,'armed':list(case.fake.armed),'manual':list(case.fake.manual_stops),'journal':str(Path(case.tmp.name)/'.container-update-app.json')}))
  os.kill(os.getpid(),signal.SIGKILL)
case.mod.fault=terminate
with case.mod.signals():case.update()
""".replace('TESTS_PATH', repr(str(base.ROOT / 'tests')))
            env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', TMPDIR=directory)
            result = subprocess.run([sys.executable, '-c', code, str(output)], capture_output=True, text=True, env=env, timeout=30)
            self.assertEqual(result.returncode, -signal.SIGKILL, result.stderr)
            saved = json.loads(output.read_text())
            record = json.loads(Path(saved['journal']).read_text())
            self.assertEqual(record['state'], 'promoting')
            fake = base.FakeDocker(record['old']); fake.containers = saved['containers']
            fake.armed, fake.manual_stops = set(saved['armed']), set(saved['manual'])
            self.assertEqual(fake.daemon_restart(), {record['new_id']})
            self.assertFalse(fake.resource_conflicts())
            module = base.embedded_module('policy-recovery-after-kill')
            module.docker, module.Engine = fake, fake.engine()
            module.offline_guard = lambda roots: None
            module.wait_ready = lambda identifier, timeout: None
            module.recover_container(saved['journal'])
            module.recover_container(saved['journal'])
            fake.advance(12)
            self.assertEqual(fake.daemon_restart(), {record['old']['Id']})
            self.assertFalse(fake.resource_conflicts())
            self.assertEqual(json.loads(Path(saved['journal']).read_text())['state'], 'recovered')

    def test_real_signals_after_isolation_and_promotion_restore_only_selected_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            for point in ('old_isolation', 'after_candidate_promotion'):
                for signum in (signal.SIGINT, signal.SIGTERM):
                    with self.subTest(point=point, signal=signum):
                        output = Path(directory) / (point + '-' + str(signum) + '.json')
                        code = '''
import sys,os,json,signal
from pathlib import Path
sys.path.insert(0, TESTS_PATH)
import test_regressions as base
case=base.ContainerTests();case.setUp();case.mod.signal=signal
old=case.fake.lookup(case.old['Id']);old['HostConfig']['RestartPolicy']={'Name':'always','MaximumRetryCount':0}
def policy_hook(container,policy):
 if sys.argv[1]=='old_isolation' and container['Id']==case.old['Id'] and policy['Name']=='no':os.kill(os.getpid(),int(sys.argv[2]))
def fault(point):
 if point==sys.argv[1]:os.kill(os.getpid(),int(sys.argv[2]))
case.fake.after_policy_update=policy_hook;case.mod.fault=fault
try:
 with case.mod.signals():case.update()
except KeyboardInterrupt:pass
record=json.loads((Path(case.tmp.name)/'.container-update-app.json').read_text())
case.fake.advance(12);running=case.fake.daemon_restart()
Path(sys.argv[3]).write_text(json.dumps({'record':record,'running':list(running),'conflicts':case.fake.resource_conflicts()}))
case.doCleanups()
'''.replace('TESTS_PATH', repr(str(base.ROOT / 'tests')))
                        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', TMPDIR=directory)
                        result = subprocess.run([sys.executable, '-c', code, point, str(int(signum)), str(output)],
                                                capture_output=True, text=True, env=env, timeout=30)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        observed = json.loads(output.read_text())
                        self.assertEqual(observed['record']['state'], 'recovered')
                        self.assertEqual(observed['record']['observed']['restart_policy']['Name'], 'always')
                        self.assertEqual(observed['running'], ['a' * 64])
                        self.assertEqual(observed['conflicts'], [])


if __name__ == '__main__':
    unittest.main()
