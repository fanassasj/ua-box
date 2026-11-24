"""Initialization orchestration: temp files and fake system commands only."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import test_regressions as base


class InitOrderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='ua-init-test-')
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        for directory in ('sysctl.d', 'security', 'systemd', 'ssh'):
            (self.work / 'etc' / directory).mkdir(parents=True)
        self.events = self.work / 'events'
        # Redirect every configuration write in the original function into this fixture.
        self.source = base.shell_function('system_init_optimize').replace(
            '/etc/', str(self.work / 'etc') + '/').replace('/tmp/.ua-', str(self.work / '.ua-'))
        self.source += '\n' + base.shell_function('system_init_check_status')
        self.source += r'''
need_root() { return 0; }
clear() { :; }
hostname() { echo fixture; }
log_action() { :; }
has_cmd() { return 1; }
free() {
  if [ "$1" = -m ]; then
    printf 'Mem: %s 0 0\nSwap: 2048 0 2048\n' "$MEM_MB"
  else
    echo status-free >> "$EVENTS"; echo 'memory and swap'; return "${FREE_FAIL:-0}"
  fi
}
df() {
  if [ "$1" = -Pk ]; then
    printf 'Filesystem Blocks Used Available Use%% Mounted\nfake 99999999 1 %s 1%% /\n' "$DISK_KB"
  else
    echo status-disk >> "$EVENTS"; echo 'disk status'; return "${DF_FAIL:-0}"
  fi
}
uptime() { echo status-load >> "$EVENTS"; return "${UPTIME_FAIL:-0}"; }
system_init_swap() {
  echo "swap:$1" >> "$EVENTS"
  [ "$1" -gt 0 ] && [ "${SWAP_FAIL:-0}" -eq 0 ]
}
system_update() { echo update >> "$EVENTS"; }
pkg_manager() { echo tools >> "$EVENTS"; echo fake; }
is_pkg_installed() { return 0; }
install_docker() { echo docker >> "$EVENTS"; }
docker() { echo 'Docker version fixture'; }
_apply_hostname() { echo hostname >> "$EVENTS"; }
get_virt() { echo LXC; }
sysctl() { echo tune >> "$EVENTS"; }
timedatectl() { :; }
systemctl() { :; }
service() { :; }
modprobe() { return 99; }
apt() { return 99; }
dnf() { return 99; }
yum() { return 99; }
apk() { return 99; }
pacman() { return 99; }
zypper() { return 99; }
system_init_optimize
'''

    def run_init(self, mem=1024, disk=8192 * 1024, answers='n\ny\n', **extra):
        env = dict(os.environ, EVENTS=str(self.events), MEM_MB=str(mem), DISK_KB=str(disk),
                   **{key: str(value) for key, value in extra.items()})
        result = subprocess.run(['bash', '-c', self.source], input=answers, env=env,
                                text=True, capture_output=True, timeout=10)
        events = self.events.read_text().splitlines() if self.events.exists() else []
        return result, events

    def test_requested_order_and_low_memory_swap_sizes(self):
        for mem in (512, 1024, 2048, 4096):
            with self.subTest(mem=mem):
                self.events.unlink(missing_ok=True)
                (self.work / 'etc/sysctl.d/99-ua-tcp-tune.conf').unlink(missing_ok=True)
                result, events = self.run_init(mem=mem)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(events[:4], ['swap:2048', 'update', 'tools', 'docker'])
                self.assertEqual(events[-3:], ['status-free', 'status-disk', 'status-load'])
                self.assertLess(events.index('docker'), events.index('tune'))
                for i, label in enumerate(('设置 Swap', '更新系统', '安装常用工具', '安装 Docker', '检查系统状态'), 1):
                    self.assertIn(f'[{i}/5] {label}...', result.stdout)
                self.assertNotIn('/7]', result.stdout)

    def test_larger_memory_keeps_existing_cap(self):
        result, events = self.run_init(mem=8192, disk=20000 * 1024)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(events[0], 'swap:8192')

    def test_disk_headroom_reduces_target(self):
        result, events = self.run_init(disk=3000 * 1024)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(events[0], 'swap:952')

    def test_swap_failure_or_insufficient_disk_stops_before_update(self):
        for extra in ({'SWAP_FAIL': 1}, {'disk': 2100 * 1024}, {'disk': 'unknown'}):
            with self.subTest(extra=extra):
                self.events.unlink(missing_ok=True)
                result, events = self.run_init(**extra)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(len(events), 1)
                self.assertTrue(events[0].startswith('swap:'))
                self.assertIn('已停止初始化', result.stdout)

    def test_cancel_has_no_operations(self):
        result, events = self.run_init(answers='n\nn\n')
        self.assertEqual(result.returncode, 0)
        self.assertEqual(events, [])

    def test_status_failure_is_reported_and_remaining_readouts_run(self):
        for command in ('FREE_FAIL', 'DF_FAIL', 'UPTIME_FAIL'):
            with self.subTest(command=command):
                self.events.unlink(missing_ok=True)
                result, events = self.run_init(**{command: 1})
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(events[-3:], ['status-free', 'status-disk', 'status-load'])
                self.assertIn('系统状态读取失败', result.stdout)
                self.assertNotIn('系统状态检查完成', result.stdout)


class InitSwapTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='ua-init-swap-test-')
        self.addCleanup(self.tmp.cleanup)
        self.work = Path(self.tmp.name)
        self.swap = self.work / 'swapfile'
        self.proc = self.work / 'swaps'
        self.fstab = self.work / 'fstab'
        self.proc.write_text('Filename Type Size Used Priority\n')
        self.fstab.write_text('# existing configuration\n')
        self.source = base.shell_function('system_init_swap')
        for original, replacement in (('/swapfile', self.swap), ('/proc/swaps', self.proc), ('/etc/fstab', self.fstab)):
            self.source = self.source.replace(original, str(replacement))
        self.source += r'''
has_cmd() { [ "${NO_FALLOCATE:-0}" -eq 0 ]; }
fallocate() { [ "$MODE" != allocation-fail ] || return 1; printf allocated >| "$SWAP"; }
dd() { [ "$MODE" != allocation-fail ] || return 1; printf allocated >| "$SWAP"; }
mkswap() { [ "$MODE" != format-fail ]; }
swapon() {
  [ "$MODE" != activation-fail ] || return 1
  if [ "$MODE" != not-active ]; then
    printf '%s file 2097152 0 -2\n' "$SWAP" >> "$PROC"
  fi
}
system_init_swap "${TARGET:-2048}"
'''

    def run_swap(self, mode='ok', **extra):
        return subprocess.run(['bash', '-c', self.source], text=True, capture_output=True, timeout=10,
                              env=dict(os.environ, MODE=mode, SWAP=str(self.swap), PROC=str(self.proc),
                                       **{key: str(value) for key, value in extra.items()}))

    def test_new_swap_verified_and_persisted_only_once(self):
        first = self.run_swap()
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(self.swap.stat().st_mode & 0o777, 0o600)
        original = self.swap.read_bytes(), self.fstab.read_bytes(), self.proc.read_bytes()
        second = self.run_swap()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(original, (self.swap.read_bytes(), self.fstab.read_bytes(), self.proc.read_bytes()))
        self.assertIn('保留现有配置', second.stdout)
        self.assertEqual(self.fstab.read_text().count(str(self.swap)), 1)

    def test_existing_active_swap_is_reused_even_if_disk_full(self):
        self.proc.write_text('Filename Type Size Used Priority\n/dev/zram0 partition 1048576 0 100\n')
        before = self.fstab.read_bytes(), self.proc.read_bytes()
        result = self.run_swap(TARGET=0)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.swap.exists())
        self.assertEqual(before, (self.fstab.read_bytes(), self.proc.read_bytes()))

    def test_existing_inactive_file_and_symlink_preserved(self):
        for link in (False, True):
            with self.subTest(link=link):
                if link:
                    self.swap.unlink()
                    self.swap.symlink_to(self.work / 'missing-user-data')
                else:
                    self.swap.write_text('user data')
                result = self.run_swap()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('保护原文件', result.stderr)
                if link:
                    self.assertTrue(self.swap.is_symlink())
                    self.assertFalse(self.swap.exists())
                else:
                    self.assertEqual(self.swap.read_text(), 'user data')
                self.assertEqual(self.fstab.read_text(), '# existing configuration\n')

    def test_allocation_format_activation_and_verification_fail_closed(self):
        for mode in ('allocation-fail', 'format-fail', 'activation-fail', 'not-active'):
            with self.subTest(mode=mode):
                self.swap.unlink(missing_ok=True)
                result = self.run_swap(mode)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('Swap 已设置为', result.stdout)
                self.assertEqual(self.fstab.read_text(), '# existing configuration\n')
                self.assertTrue(self.swap.exists(), 'retain partial file for inspection')

    def test_disk_full_does_not_create_any_file(self):
        result = self.run_swap(TARGET=0)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.swap.exists())

    def test_dd_fallback_is_verified(self):
        result = self.run_swap(NO_FALLOCATE=1)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.swap.read_text(), 'allocated')
        self.assertIn(str(self.swap), self.proc.read_text())

    def test_persistence_failure_keeps_activated_swap(self):
        self.fstab.unlink()
        self.fstab.mkdir()  # writing this path fails even under root
        result = self.run_swap()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(str(self.swap), self.proc.read_text())
        self.assertTrue(self.swap.exists())
        self.assertNotIn('Swap 已设置为', result.stdout)


if __name__ == '__main__':
    unittest.main()
