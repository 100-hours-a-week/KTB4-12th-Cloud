"""원격 실행 없는 scrape/대시보드 계약 검사."""
import ast
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('scrape', Path(__file__).with_name('prepare-host-metrics-scrape.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class HostScrape(unittest.TestCase):
    def test_remote_syntax_and_exact_target(self):
        ast.parse(module.REMOTE)
        self.assertEqual(module.build_request()['InstanceIds'], ['i-0c6a75f630436591c'])

    def test_hosts_and_dashboard(self):
        self.assertEqual(len(module.HOSTS), 3)
        self.assertEqual({h['targets'][0] for h in module.HOSTS},
                         {'172.31.6.212:9100', '10.20.10.236:9100', '10.20.11.100:9100'})
        panels = module.dashboard()['panels']
        self.assertEqual(len(panels), 6)
        self.assertEqual({p['datasource']['uid'] for p in panels}, {'seonjalal-prometheus'})

    def test_preflight_before_mutation(self):
        remote = module.REMOTE
        self.assertLess(remote.index('fcntl.flock('), remote.index('config.read_bytes()'))
        self.assertLess(remote.index('HOST_CONNECTIVITY_AND_METRICS_VERIFIED=3'), remote.index('write_same_inode(new)'))
        self.assertLess(remote.index("'check', 'config'"), remote.index('write_same_inode(new)'))

    def test_bind_mount_inode_and_rollback(self):
        for value in ("config.open('r+b')", 'os.fsync(', 'write_same_inode(old)',
                      "'--signal=HUP'", 'PROMETHEUS_PREVIOUS_CONFIG_RESTORED'):
            self.assertIn(value, module.REMOTE)
        self.assertNotIn('os.replace(', module.REMOTE)

    def test_no_credentials_reset_or_volume_delete(self):
        for value in ('password_file', 'GF_SECURITY_ADMIN', "'volume', 'rm'", 'shutil.rmtree', "'stop'"):
            self.assertNotIn(value, module.REMOTE)
        self.assertIn("docker('restart', '--time', '30', 'seonjalal-monitor-grafana')", module.REMOTE)

    def test_exact_three_up_and_browser_limit(self):
        self.assertIn("len(nodes) == 3 and all(item.get('health') == 'up'", module.REMOTE)
        self.assertIn('Dashboard browser verification', module.REMOTE)
        self.assertIn('static host targets', module.REMOTE)


if __name__ == '__main__':
    unittest.main()
