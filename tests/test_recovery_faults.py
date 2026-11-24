"""Fault injection is confined to fake Docker and private temporary files/processes."""
import contextlib
import copy
import errno
import hashlib
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


class ContainerRecoveryFaults(unittest.TestCase):
    def setUp(self):
        base.ContainerTests.setUp(self)

    def update(self):
        return self.mod.update_container('app', self.tmp.name, '5')

    def journal(self):
        return Path(self.tmp.name) / '.container-update-app.json'

    def test_network_reconnect_failure_still_starts_and_records_old_state(self):
        self.fake.fail_create = True
        self.fake.fail_connect.add('private')
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(RuntimeError):
            self.update()
        self.assertIn(('start', self.old['Id']), self.calls)
        record = json.loads(self.journal().read_text())
        self.assertEqual(record['state'], 'recovery_incomplete')
        self.assertTrue(record['observed']['state']['Running'])
        self.assertEqual(record['observed']['name'], '/app')
        self.assertNotIn('已验证旧容器', output.getvalue())
        self.assertTrue(any(e['step'] == 'connect:private' for e in record['errors']))

    def test_recovery_log_enospc_cannot_skip_old_start(self):
        self.fake.fail_create = True
        self.fake.fail_connect.add('private')
        save = self.mod.save_json
        def full_disk(path, record):
            if Path(path) == self.journal() and record.get('events'):
                raise OSError(errno.ENOSPC, 'journal disk full')
            return save(path, record)
        with mock.patch.object(self.mod, 'save_json', side_effect=full_disk):
            with self.assertRaises(RuntimeError):
                self.update()
        self.assertIn(('start', self.old['Id']), self.calls)
        self.assertTrue(self.fake.lookup(self.old['Id'])['State']['Running'])

    def test_multiple_network_failures_are_independent_of_start(self):
        self.fake.lookup('app')['NetworkSettings']['Networks']['second'] = {'Aliases': ['alias-two']}
        self.fake.fail_create = True
        self.fake.fail_connect.update(['private', 'second'])
        with self.assertRaises(RuntimeError):
            self.update()
        paths = [path for _, path, _ in self.request_calls]
        self.assertIn('/networks/private/connect', paths)
        self.assertIn('/networks/second/connect', paths)
        self.assertIn(('start', self.old['Id']), self.calls)

    def test_network_state_lookup_failure_cannot_skip_start(self):
        self.fake.fail_create = True
        def failed_lookup(point):
            if point == 'before_restore_network':
                raise TimeoutError('network state lookup unavailable')
        self.mod.fault = failed_lookup
        with self.assertRaises(RuntimeError):
            self.update()
        self.assertIn(('start', self.old['Id']), self.calls)
        self.assertTrue(self.fake.lookup(self.old['Id'])['State']['Running'])
        record = json.loads(self.journal().read_text())
        self.assertEqual(record['state'], 'recovery_incomplete')
        self.assertTrue(any(e['step'] == 'connect:private' for e in record['errors']))

    def test_failed_old_start_never_reports_recovery_success(self):
        docker = self.mod.docker
        def fail_start(*args):
            if args == ('start', self.old['Id']):
                raise subprocess.CalledProcessError(1, args, stderr='old start failed')
            return docker(*args)
        self.mod.docker = fail_start
        self.fake.fail_create = True
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(RuntimeError):
            self.update()
        record = json.loads(self.journal().read_text())
        self.assertFalse(record['observed']['state']['Running'])
        self.assertEqual(record['state'], 'recovery_incomplete')
        self.assertNotIn('已验证旧容器', output.getvalue())

    def test_rename_server_success_then_client_interruption_reconciles_by_id(self):
        def interrupt(container):
            raise self.mod.Interrupted('daemon renamed, client interrupted')
        self.fake.after_rename = interrupt
        with self.assertRaises(KeyboardInterrupt):
            self.update()
        actual = self.fake.lookup(self.old['Id'])
        self.assertEqual(actual['Name'], '/app')
        self.assertTrue(actual['State']['Running'])
        self.assertEqual(json.loads(self.journal().read_text())['state'], 'recovered')

    def test_create_response_lost_discovers_owned_candidate_without_deleting_others(self):
        def lost_response(container):
            raise TimeoutError('daemon created candidate; response was lost')
        self.fake.after_create = lost_response
        with self.assertRaises(TimeoutError):
            self.update()
        self.assertEqual(self.fake.lookup(self.old['Id'])['Name'], '/app')
        self.assertEqual(json.loads(self.journal().read_text())['state'], 'recovered')
        self.assertFalse(any(c[0] == 'rm' for c in self.calls))
        self.assertFalse(self.fake.lookup(self.fake.new_ids[-1])['State']['Running'])

    def test_foreign_container_reusing_name_is_not_touched_or_given_old_data(self):
        def foreign_after_start(container):
            foreign = copy.deepcopy(container)
            foreign['Id'], foreign['Config']['Labels'] = 'foreign-id', {}
            del self.fake.containers[container['Id']]
            self.fake.containers['foreign-id'] = foreign
            raise RuntimeError('candidate disappeared and name reused')
        self.fake.after_start = foreign_after_start
        with self.assertRaises(RuntimeError):
            self.update()
        self.assertTrue(self.fake.lookup('foreign-id')['State']['Running'])
        self.assertEqual(self.fake.lookup('foreign-id')['Name'], '/app')
        self.assertFalse(any('foreign-id' in call for call in self.calls if call[0] in ('stop', 'rename', 'rm')))
        self.assertEqual(json.loads(self.journal().read_text())['state'], 'recovery_incomplete')
        self.assertFalse(self.fake.lookup(self.old['Id'])['State']['Running'])

    def test_repeated_recovery_preserves_writes_after_old_container_restarted(self):
        self.fake.fail_create = True
        self.fake.fail_connect.add('private')
        with self.assertRaises(RuntimeError):
            self.update()
        with sqlite3.connect(self.data / 'data.db') as database:
            database.execute("INSERT INTO sample VALUES ('after-recovery')")
        self.fake.fail_connect.clear()
        self.mod.recover_container(self.journal())
        self.mod.recover_container(self.journal())
        with sqlite3.connect(self.data / 'data.db') as database:
            self.assertEqual(database.execute('SELECT value FROM sample ORDER BY rowid').fetchall(),
                             [('before',), ('after-recovery',)])
        self.assertEqual(json.loads(self.journal().read_text())['state'], 'recovered')

    def test_new_schema_and_data_changes_are_discarded_by_whole_volume_rollback(self):
        before = self.mod.inventory(self.data)
        original_identity = self.mod.identity(self.data)
        def migrate(container):
            with sqlite3.connect(self.data / 'data.db') as database:
                database.execute('ALTER TABLE sample ADD COLUMN migrated TEXT')
                database.execute("UPDATE sample SET value='new-version'")
            (self.data / 'new-version-only').write_text('new data')
        self.fake.after_start = migrate
        def fail_new_only(identifier, timeout):
            if identifier in self.fake.new_ids:
                raise RuntimeError('migration then unhealthy')
        self.mod.wait_ready.side_effect = fail_new_only
        with self.assertRaises(RuntimeError):
            self.update()
        self.assertEqual(self.mod.identity(self.data), original_identity)
        self.assertEqual(self.mod.inventory(self.data), before)
        record = json.loads(self.journal().read_text())
        data = json.loads(Path(record['data_journal']).read_text())
        changed_copy = Path(data['entries'][0]['slot'])
        self.assertTrue((changed_copy / 'new-version-only').exists())
        self.assertEqual(record['state'], 'recovered')

    def test_external_database_and_unbounded_mount_are_blocked_before_stop(self):
        for setting in ('external', 'root-mount'):
            with self.subTest(setting=setting):
                live = self.fake.lookup(self.old['Id'])
                live['Config']['Env'] = ['DB_MYSQL_HOST=external'] if setting == 'external' else []
                live['Mounts'][0]['Source'] = '/' if setting == 'root-mount' else str(self.data)
                self.calls.clear()
                with self.assertRaises(self.mod.RecoveryError):
                    self.update()
                self.assertFalse(any(c[0] == 'stop' for c in self.calls))

    def test_shared_writable_volume_blocks_switch_and_restores_old(self):
        peer = copy.deepcopy(self.old)
        peer['Id'], peer['Name'] = 'peer-id', '/peer'
        self.fake.containers['peer-id'] = peer
        # Use the real guard against fake Docker; it must find the peer before scanning /proc.
        self.mod.offline_guard = self.real_offline_guard
        with self.assertRaises(self.mod.RecoveryError):
            self.update()
        self.assertTrue(self.fake.lookup(self.old['Id'])['State']['Running'])
        self.assertTrue(self.fake.lookup('peer-id')['State']['Running'])
        self.assertFalse(self.fake.new_ids)

    def test_unreadable_host_process_blocks_real_guard_without_losing_original_data(self):
        self.fake.fail_create = True
        # Keep the update in its fixture world, then exercise the REAL guard
        # during rollback with the same /proc permission failure seen in CI.
        self.fake.after_rename = lambda container: setattr(self.mod, 'offline_guard', self.real_offline_guard)
        original_iterdir = self.mod.Path.iterdir
        def process_view(path):
            if path == Path('/proc'):
                return iter([Path('/proc/1')])
            if path == Path('/proc/1/fd'):
                raise PermissionError(errno.EACCES, 'isolated permission fault')
            return original_iterdir(path)
        output = io.StringIO()
        with mock.patch.object(self.mod.Path, 'iterdir', autospec=True, side_effect=process_view), \
             mock.patch.object(self.mod.shutil, 'which', side_effect=lambda name: '/fake/docker' if name == 'docker' else None), \
             contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            with self.assertRaises(RuntimeError):
                self.update()
        record = json.loads(self.journal().read_text())
        self.assertEqual(record['state'], 'recovery_incomplete')
        self.assertTrue(any(error['step'] == 'data_rollback' and '无法确认进程停写状态' in error['error'] for error in record['errors']))
        self.assertFalse(self.fake.lookup(self.old['Id'])['State']['Running'])
        self.assertEqual(self.fake.lookup(self.old['Id'])['HostConfig']['RestartPolicy'], self.mod.NO_RESTART)
        self.assertNotIn(('start', self.old['Id']), self.calls)
        self.assertNotIn('已验证旧容器', output.getvalue())
        data = json.loads(Path(record['data_journal']).read_text())
        for entry in data['entries']:
            self.assertEqual(self.mod.identity(entry['slot']), entry['old_id'])
            self.assertEqual(self.mod.inventory(Path(entry['slot'])), entry['before'])

    def test_second_update_keeps_original_registry_reference_and_data_protection(self):
        self.update()
        self.update()
        pulls = [call[1] for call in self.calls if call[0] == 'pull']
        self.assertEqual(pulls, ['app:latest', 'app:latest'])
        self.assertEqual(len(self.fake.new_ids), 2)
        self.assertTrue(self.fake.lookup('app')['State']['Running'])
        records = list(Path(self.tmp.name).glob('data-*.json'))
        self.assertEqual(len(records), 2)

    def test_original_stopped_state_is_preserved_on_failure(self):
        self.fake.lookup(self.old['Id'])['State']['Running'] = False
        self.fake.fail_create = True
        with self.assertRaises(RuntimeError):
            self.update()
        self.assertFalse(self.fake.lookup(self.old['Id'])['State']['Running'])
        self.assertNotIn(('start', self.old['Id']), self.calls)
        self.assertEqual(json.loads(self.journal().read_text())['state'], 'recovered')


class SimulatedOfflineGuardTests(unittest.TestCase):
    def setUp(self):
        base.ContainerTests.setUp(self)

    def test_guard_tracks_actual_stop_and_start_state(self):
        with self.assertRaisesRegex(RuntimeError, 'simulated mount still in use'):
            self.fake.offline_guard([self.data])
        self.fake('stop', self.old['Id'])
        self.fake.offline_guard([self.data])
        self.fake('start', self.old['Id'])
        with self.assertRaisesRegex(RuntimeError, 'simulated mount still in use'):
            self.fake.offline_guard([self.data])
        self.assertEqual(len(self.fake.offline_checks), 3)

    def test_running_peer_and_nested_mount_block_even_when_old_is_excluded(self):
        peer = copy.deepcopy(self.old)
        peer['Id'], peer['Name'] = 'peer-id', '/peer'
        peer['Mounts'][0]['Source'] = str(self.data / 'nested')
        self.fake.containers[peer['Id']] = peer
        with self.assertRaisesRegex(RuntimeError, 'peer-id'):
            self.fake.offline_guard([self.data], exclude=(self.old['Id'],))

    def test_guard_matches_real_policy_for_readonly_mount_and_unrelated_path(self):
        # The production guard conservatively rejects any live mount user.
        self.fake.lookup(self.old['Id'])['Mounts'][0]['RW'] = False
        with self.assertRaises(RuntimeError):
            self.fake.offline_guard([self.data])
        self.fake.offline_guard([self.data.with_name(self.data.name + '-unrelated')])


class DirectoryAndDatabaseFaults(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        self.mod = base.embedded_module('common')
        self.source, self.target = self.work / 'source', self.work / 'target'
        self.source.mkdir()
        self.target.mkdir()
        (self.source / 'data').write_text('restored')
        (self.target / 'data').write_text('original')
        (self.target / 'stale-journal.fixture').write_text('keep-original-only')
        self.journal = self.work / 'journal.json'

    def prepare(self):
        return self.mod.DirectoryTransaction.prepare([(self.target, self.source)], self.journal)

    def test_complete_directory_switch_keeps_extra_files_only_in_original(self):
        original = self.mod.inventory(self.target)
        original_id = self.mod.identity(self.target)
        transaction = self.prepare()
        transaction.commit()
        self.assertEqual(set(p.name for p in self.target.iterdir()), {'data'})
        saved = Path(transaction.data['entries'][0]['slot'])
        self.assertEqual(self.mod.inventory(saved), original)
        self.assertEqual(self.mod.identity(saved), original_id)
        transaction.rollback()
        self.assertEqual(self.mod.identity(self.target), original_id)
        self.assertEqual(self.mod.inventory(self.target), original)
        transaction.rollback()
        self.assertEqual(self.mod.inventory(self.target), original)

    def test_copy_failure_does_not_touch_original_or_overwrite_unique_backup(self):
        original, inode = self.mod.inventory(self.target), self.mod.identity(self.target)
        def partial_copy(src, dst, *args, **kwargs):
            Path(dst).write_bytes(b'partial')
            raise OSError(errno.EIO, 'copy interrupted')
        with mock.patch.object(self.mod.shutil, 'copy2', side_effect=partial_copy):
            with self.assertRaises(OSError):
                self.prepare()
        self.assertEqual(self.mod.inventory(self.target), original)
        self.assertEqual(self.mod.identity(self.target), inode)

    def test_disk_space_preflight_fails_before_any_exchange(self):
        with mock.patch.object(self.mod.shutil, 'disk_usage', return_value=type('Disk', (), {'free': 0})()):
            with self.assertRaises(self.mod.RecoveryError):
                self.prepare()
        self.assertEqual((self.target / 'data').read_text(), 'original')

    def test_enospc_during_copy_preserves_original(self):
        original = self.mod.inventory(self.target)
        def out_of_space(point):
            if point == 'after_copy_file':
                raise OSError(errno.ENOSPC, 'disk full while copying')
        self.mod.fault = out_of_space
        with self.assertRaises(OSError):
            self.prepare()
        self.assertEqual(self.mod.inventory(self.target), original)

    def test_exchange_failure_uses_rename_rollback_and_never_copy_over_original(self):
        transaction = self.prepare()
        original_id = transaction.data['entries'][0]['old_id']
        def interrupted(point):
            if point == 'after_exchange':
                raise OSError(errno.EIO, 'interrupted after exchange')
        self.mod.fault = interrupted
        with self.assertRaises(OSError):
            transaction.commit()
        self.mod.fault = lambda point: None
        with mock.patch.object(self.mod.shutil, 'copy2', side_effect=AssertionError('rollback must never copy')):
            self.mod.DirectoryTransaction(self.journal).rollback()
        self.assertEqual(self.mod.identity(self.target), original_id)
        self.assertEqual((self.target / 'data').read_text(), 'original')

    def test_multi_root_partial_commit_recovers_all_originals(self):
        second = self.work / 'second'
        second.mkdir()
        (second / 'data').write_text('second-original')
        transaction = self.mod.DirectoryTransaction.prepare([(self.target, self.source), (second, self.source)], self.journal)
        count = 0
        def fail_second(point):
            nonlocal count
            if point == 'before_exchange':
                count += 1
                if count == 2:
                    raise OSError('second switch failed')
        self.mod.fault = fail_second
        with self.assertRaises(OSError):
            transaction.commit()
        self.mod.fault = lambda point: None
        self.mod.DirectoryTransaction(self.journal).rollback()
        self.assertEqual((self.target / 'data').read_text(), 'original')
        self.assertEqual((second / 'data').read_text(), 'second-original')

    def test_unknown_replacement_is_never_overwritten_by_recovery(self):
        transaction = self.prepare()
        transaction.commit()
        moved = self.work / 'third-party-data'
        os.rename(self.target, moved)
        self.target.mkdir()
        (self.target / 'user-file').write_text('do not touch')
        with self.assertRaises(self.mod.RecoveryError):
            transaction.rollback()
        self.assertEqual((self.target / 'user-file').read_text(), 'do not touch')
        self.assertTrue(Path(transaction.data['entries'][0]['slot']).exists())

    def sqlite_tree(self):
        source = self.work / 'kuma'
        source.mkdir()
        with sqlite3.connect(source / 'kuma.db') as db:
            db.execute('CREATE TABLE sample(value TEXT)')
            db.execute("INSERT INTO sample VALUES ('confirmed')")
        return source

    def make_sqlite_archive(self):
        source = self.sqlite_tree()
        archive = self.work / 'backup.tar.gz'
        self.mod.build_archive([('root/uptime-kuma', source)], archive, guard=lambda roots: None)
        return source, archive

    def repack(self, archive, transform):
        rows = []
        with tarfile.open(archive) as tar:
            for member in tar:
                rows.append((member, tar.extractfile(member).read() if member.isfile() else None))
        rows = transform(rows)
        with tarfile.open(archive, 'w:gz') as tar:
            for member, content in rows:
                if content is not None:
                    member.size = len(content)
                    tar.addfile(member, io.BytesIO(content))
                else:
                    tar.addfile(member)

    def test_running_native_caddy_is_not_assumed_quiescent(self):
        with mock.patch.object(self.mod.shutil, 'which', side_effect=lambda name: '/fake/systemctl' if name == 'systemctl' else None), mock.patch.object(self.mod.subprocess, 'run', return_value=type('Status', (), {'returncode': 0})()):
            with self.assertRaises(self.mod.RecoveryError):
                self.mod.offline_guard([Path('/root/caddy')])

    def test_panel_directory_cannot_be_replaced_by_a_file_root(self):
        source = self.work / 'not-a-directory'
        source.write_text('invalid directory root')
        archive = self.work / 'wrong-root.tar.gz'
        self.mod.build_archive([('root/caddy', source)], archive, guard=lambda roots: None)
        destination = self.work / 'restore-root'
        destination.mkdir()
        with self.assertRaises(self.mod.RecoveryError):
            self.mod.restore_archive(archive, True, '/root/ua-box/config', '/root/.config/rclone/rclone.conf',
                                     self.work / 'backups', root=destination, guard=lambda roots: None)
        self.assertFalse((destination / 'root/caddy').exists())

    def test_sqlite_backup_has_independent_restore_verification(self):
        source, archive = self.make_sqlite_archive()
        original = self.mod.inventory(source)
        with self.mod.verified_archive(archive) as (stage, manifest):
            restored = stage / 'root/uptime-kuma/kuma.db'
            with sqlite3.connect(restored) as db:
                self.assertEqual(db.execute('PRAGMA integrity_check').fetchone(), ('ok',))
                self.assertEqual(db.execute('SELECT value FROM sample').fetchall(), [('confirmed',)])
            self.assertEqual(manifest['databases']['root/uptime-kuma']['kuma.db']['engine'], 'sqlite')
        self.assertEqual(self.mod.inventory(source), original)
        self.assertEqual(self.mod.main(['verify', str(archive)]), 0)

    def test_crash_left_sqlite_wal_is_included_in_backup_without_touching_source(self):
        source = self.work / 'wal-kuma'
        source.mkdir()
        code = "import sqlite3,os,sys; d=sqlite3.connect(sys.argv[1]); d.execute('PRAGMA journal_mode=WAL'); d.execute('CREATE TABLE sample(value TEXT)'); d.execute(\"INSERT INTO sample VALUES ('wal-committed')\"); d.commit(); os._exit(0)"
        result = subprocess.run([sys.executable, '-c', code, str(source / 'kuma.db')], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((source / 'kuma.db-wal').exists())
        before = self.mod.inventory(source)
        archive = self.work / 'wal.tar.gz'
        self.mod.build_archive([('root/uptime-kuma', source)], archive, guard=lambda roots: None)
        self.assertEqual(self.mod.inventory(source), before)
        with self.mod.verified_archive(archive) as (stage, manifest):
            with sqlite3.connect(stage / 'root/uptime-kuma/kuma.db') as db:
                self.assertEqual(db.execute('SELECT value FROM sample').fetchall(), [('wal-committed',)])
            self.assertFalse((stage / 'root/uptime-kuma/kuma.db-wal').exists())

    def test_missing_database_is_rejected_even_with_self_consistent_file_manifest(self):
        source, archive = self.make_sqlite_archive()
        def omit(rows):
            output = []
            for member, content in rows:
                if member.name == 'root/uptime-kuma/kuma.db':
                    continue
                if member.name == 'UA-MANIFEST.json':
                    data = json.loads(content)
                    data['entries'].pop('root/uptime-kuma/kuma.db')
                    data['databases']['root/uptime-kuma'] = {}
                    content = json.dumps(data).encode()
                output.append((member, content))
            return output
        self.repack(archive, omit)
        with self.assertRaises(self.mod.RecoveryError):
            with self.mod.verified_archive(archive):
                self.fail('incomplete database archive accepted')

    def test_corrupt_database_is_rejected_even_if_hash_is_updated(self):
        source, archive = self.make_sqlite_archive()
        damaged = b'SQLite format 3\0' + b'broken' * 40
        def corrupt(rows):
            for member, content in rows:
                if member.name == 'root/uptime-kuma/kuma.db':
                    content = damaged
                if member.name == 'UA-MANIFEST.json':
                    data = json.loads(content)
                    data['entries']['root/uptime-kuma/kuma.db'].update(size=len(damaged), sha256=hashlib.sha256(damaged).hexdigest())
                    content = json.dumps(data).encode()
                yield member, content
        self.repack(archive, corrupt)
        with self.assertRaises((self.mod.RecoveryError, sqlite3.DatabaseError)):
            with self.mod.verified_archive(archive):
                self.fail('corrupt database accepted')

    def test_missing_file_count_and_hash_are_checked(self):
        source, archive = self.make_sqlite_archive()
        self.repack(archive, lambda rows: [(member, content) for member, content in rows if member.name != 'root/uptime-kuma/kuma.db'])
        with self.assertRaises(self.mod.RecoveryError):
            with self.mod.verified_archive(archive):
                self.fail('missing file accepted')

    def test_unknown_and_external_databases_fail_closed(self):
        for data in [b'not a valid database', b'']:
            (self.source / 'unknown.db').write_bytes(data)
            with self.assertRaises(self.mod.RecoveryError):
                self.mod.inspect_databases(self.source, normalize=True)
        (self.source / 'unknown.db').unlink()
        (self.source / 'config.json').write_text(json.dumps({'database': {'type': 'postgres'}}))
        with self.assertRaises(self.mod.RecoveryError):
            self.mod.inspect_databases(self.source)

    def test_bbolt_cannot_be_claimed_verified_without_its_real_checker(self):
        header = bytearray(64)
        import struct
        struct.pack_into('<I', header, 16, 0xED0CDAED)
        (self.source / 'filebrowser.db').write_bytes(header)
        with mock.patch.object(self.mod.shutil, 'which', return_value=None):
            with self.assertRaises(self.mod.RecoveryError):
                self.mod.inspect_databases(self.source)

    def test_real_bbolt_copy_and_independent_archive_verification(self):
        self.assertIsNotNone(shutil.which('bbolt'), 'bbolt v1.4.3 must be installed for the full recovery suite')
        source = self.work / 'filebrowser'
        source.mkdir()
        shutil.copy2(base.ROOT / 'tests/fixtures/filebrowser.bbolt', source / 'filebrowser.db')
        archive = self.work / 'bbolt.tar.gz'
        self.mod.build_archive([('root/filebrowser', source)], archive, guard=lambda roots: None)
        with self.mod.verified_archive(archive) as (stage, manifest):
            self.assertEqual(manifest['databases']['root/filebrowser']['filebrowser.db']['engine'], 'bbolt')
            value = subprocess.check_output(['bbolt', 'get', str(stage / 'root/filebrowser/filebrowser.db'), 'users', 'admin'], text=True)
            self.assertEqual(value.strip(), 'fixture-user')

    def test_real_bbolt_corruption_never_passes_as_verified_backup(self):
        self.assertIsNotNone(shutil.which('bbolt'), 'bbolt required for corruption verification')
        source = self.work / 'filebrowser'
        source.mkdir()
        data = bytearray((base.ROOT / 'tests/fixtures/filebrowser.bbolt').read_bytes())
        data[72] ^= 0xFF
        data[4096 + 72] ^= 0xFF
        (source / 'filebrowser.db').write_bytes(data)
        with self.assertRaises(subprocess.CalledProcessError):
            self.mod.inspect_databases(source)

    def test_legacy_unmanifested_archive_is_not_a_verified_database_backup(self):
        archive = self.work / 'legacy.tar.gz'
        with tarfile.open(archive, 'w:gz') as tar:
            tar.add(self.source, arcname='root/caddy')
        with self.assertRaises(self.mod.RecoveryError):
            with self.mod.verified_archive(archive):
                self.fail('legacy archive accepted as verified')


class ActualSignalFaults(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        self.program = self.work / 'program.py'
        self.program.write_text(base.SCRIPT.split("<<'PY_UA_RECOVERY'\n", 1)[1].split('\nPY_UA_RECOVERY', 1)[0])

    def child(self, code, *args):
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', TMPDIR=str(self.work))
        env.pop('UA_BOX_TMP_DIR', None)
        return subprocess.run([sys.executable, '-c', code, str(self.program), *map(str, args)],
                              capture_output=True, text=True, env=env, timeout=30)

    def test_real_sigint_and_sigterm_after_container_rename_recover(self):
        for signum in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(signal=signum):
                output = self.work / ('result-' + str(signum) + '.json')
                code = '''
import sys,json,os,signal
sys.path.insert(0, '/root/ua-box/tests')
import test_regressions as base
case=base.ContainerTests(); case.setUp()
case.mod.signal=signal
sent=False
def interrupt(point):
 global sent
 if point=='after_container_rename' and not sent:
  sent=True; os.kill(os.getpid(),int(sys.argv[2]))
case.mod.fault=interrupt
try:
 with case.mod.signals():
  case.update()
except KeyboardInterrupt:
 pass
record=json.loads((__import__('pathlib').Path(case.tmp.name)/'.container-update-app.json').read_text())
__import__('pathlib').Path(sys.argv[3]).write_text(json.dumps(record))
case.doCleanups()
'''
                # Use the repository path dynamically for CI checkouts.
                code = code.replace('/root/ua-box/tests', str(base.ROOT / 'tests'))
                result = self.child(code, int(signum), output)
                self.assertEqual(result.returncode, 0, result.stderr)
                record = json.loads(output.read_text())
                self.assertEqual(record['state'], 'recovered')
                self.assertEqual(record['observed']['name'], '/app')
                self.assertTrue(record['observed']['state']['Running'])

    def test_sigkill_after_container_rename_recovers_with_fresh_process_state(self):
        state_file = self.work / 'daemon-state.json'
        code = """
import sys,json,os,signal
from pathlib import Path
sys.path.insert(0, TESTS_PATH)
import test_regressions as base
case=base.ContainerTests();case.setUp();case.mod.signal=signal
def terminate(point):
 if point=='after_container_rename':
  Path(sys.argv[2]).write_text(json.dumps({'containers':case.fake.containers,'journal':str(Path(case.tmp.name)/'.container-update-app.json')}))
  os.kill(os.getpid(),signal.SIGKILL)
case.mod.fault=terminate
with case.mod.signals():case.update()
""".replace('TESTS_PATH', repr(str(base.ROOT / 'tests')))
        result = self.child(code, state_file)
        self.assertEqual(result.returncode, -signal.SIGKILL)
        saved = json.loads(state_file.read_text())
        record = json.loads(Path(saved['journal']).read_text())
        module = base.embedded_module('fresh-recovery')
        fake = base.FakeDocker(record['old'])
        fake.containers = saved['containers']
        module.docker, module.Engine = fake, fake.engine()
        # A freshly loaded module defaults to the host /proc guard. Reconnect
        # the simulated daemon's safety check too; CI must not scan host PIDs.
        module.offline_guard = fake.offline_guard
        module.wait_ready = lambda identifier, timeout: None
        original = fake.lookup(record['old']['Id'])
        self.assertEqual(record['state'], 'replacing')
        self.assertEqual(original['Name'], '/' + record['backup_name'])
        self.assertFalse(original['State']['Running'])
        self.assertEqual(module.restart_policy(original), module.NO_RESTART)
        self.assertFalse(record.get('new_id'))
        data = json.loads(Path(record['data_journal']).read_text())
        for entry in data['entries']:
            self.assertEqual(module.identity(entry['target']), entry['new_id'])
            self.assertEqual(module.identity(entry['slot']), entry['old_id'])
            self.assertEqual(module.inventory(Path(entry['slot'])), entry['before'])
        module.recover_container(saved['journal'])
        module.recover_container(saved['journal'])
        observed = fake.lookup(record['old']['Id'])
        self.assertEqual(observed['Name'], '/app')
        self.assertTrue(observed['State']['Running'])
        self.assertEqual(module.restart_policy(observed), module.restart_policy(record['old']))
        final = json.loads(Path(saved['journal']).read_text())
        self.assertEqual(final['state'], 'recovered')
        self.assertEqual(final['errors'], [])
        self.assertTrue(fake.offline_checks)
        for entry in data['entries']:
            self.assertEqual(module.identity(entry['target']), entry['old_id'])
            self.assertEqual(module.inventory(Path(entry['target'])), entry['before'])
            self.assertTrue(Path(entry['slot']).exists())
        self.assertEqual(json.loads(Path(record['data_journal']).read_text())['state'], 'rolled_back')
        self.assertTrue(list(Path(saved['journal']).parent.glob('container-app-*.json')))
        self.assertEqual(fake.daemon_restart(), {record['old']['Id']})
        self.assertEqual(fake.resource_conflicts(), [])

    def test_real_sigint_and_sigterm_during_copy_do_not_touch_original(self):
        for signum in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(signal=signum):
                target = self.work / ('copy-target-' + str(signum))
                source = self.work / ('copy-source-' + str(signum))
                target.mkdir(); source.mkdir()
                (target / 'data').write_text('original')
                (source / 'data').write_text('new')
                journal = self.work / ('copy-journal-' + str(signum) + '.json')
                code = """
import importlib.util,sys,os
spec=importlib.util.spec_from_file_location('recovery',sys.argv[1]);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
def terminate(point):
 if point=='after_copy_file':os.kill(os.getpid(),int(sys.argv[5]))
m.fault=terminate
try:
 with m.signals():m.DirectoryTransaction.prepare([(m.Path(sys.argv[2]),m.Path(sys.argv[3]))],sys.argv[4])
except KeyboardInterrupt:raise SystemExit(75)
"""
                result = self.child(code, target, source, journal, int(signum))
                self.assertEqual(result.returncode, 75, result.stderr)
                self.assertEqual((target / 'data').read_text(), 'original')

    def transaction_child(self, signum):
        target = self.work / ('target-' + str(signum))
        source = self.work / ('source-' + str(signum))
        target.mkdir(); source.mkdir()
        (target / 'data').write_text('original')
        (source / 'data').write_text('replacement')
        journal = self.work / ('journal-' + str(signum) + '.json')
        code = '''
import importlib.util,sys,os
spec=importlib.util.spec_from_file_location('recovery',sys.argv[1]);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
t=m.DirectoryTransaction.prepare([(m.Path(sys.argv[2]),m.Path(sys.argv[3]))],sys.argv[4])
def interrupt(point):
 if point=='after_exchange': os.kill(os.getpid(),int(sys.argv[5]))
m.fault=interrupt
try:
 with m.signals(): t.commit()
except BaseException:
 with m.signals(False): t.rollback()
 raise SystemExit(75)
'''
        result = self.child(code, target, source, journal, int(signum))
        return result, target, journal

    def test_real_sigint_and_sigterm_after_directory_exchange_restore_original(self):
        for signum in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(signal=signum):
                result, target, journal = self.transaction_child(signum)
                self.assertEqual(result.returncode, 75, result.stderr)
                self.assertEqual((target / 'data').read_text(), 'original')
                self.assertEqual(json.loads(journal.read_text())['state'], 'rolled_back')

    def test_sigkill_after_exchange_is_recoverable_from_durable_journal(self):
        result, target, journal = self.transaction_child(signal.SIGKILL)
        self.assertEqual(result.returncode, -signal.SIGKILL)
        self.assertEqual(json.loads(journal.read_text())['state'], 'switching')
        module = base.embedded_module('common')
        transaction = module.DirectoryTransaction(journal)
        transaction.rollback()
        transaction.rollback()
        self.assertEqual((target / 'data').read_text(), 'original')
        self.assertEqual(json.loads(journal.read_text())['state'], 'rolled_back')


if __name__ == '__main__':
    unittest.main()
