"""수집기 설정 복구 요청의 로컬 검사. 실제 서비스 실행 없음."""
import ast
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('repair', Path(__file__).with_name('prepare-cloudwatch-exporter-repair.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class Repair(unittest.TestCase):
    def test_generated_python_and_scope(self):
        ast.parse(module.build_remote())
        self.assertEqual(module.build_request()['InstanceIds'], ['i-0c6a75f630436591c'])

    def test_old_config_reconstructed_for_exact_guard(self):
        old = module.original_configuration()
        metrics = [m for j in old['static'] for m in j['metrics']]
        self.assertEqual(len(metrics), 28)
        self.assertEqual(sum(m['statistics'] == ['p95'] for m in metrics), 1)
        self.assertTrue(all(m['delay'] == 120 for m in metrics))

    def test_new_request_uses_fixed_statistic_contract(self):
        metrics = [m for j in module.installer.configuration()['static'] for m in j['metrics']]
        self.assertEqual(len(metrics), 27)
        self.assertTrue(all('delay' not in m for m in metrics))
        self.assertTrue(all(set(m['statistics']) <= {'Average', 'Minimum', 'Maximum', 'Sum'} for m in metrics))

    def test_no_redownload_reinstall_or_credential_reset(self):
        remote = module.build_remote()
        for text in ('releases/download', "run('useradd'", 'AWS_ACCESS_KEY_ID', 'password_file',
                     "run('docker', 'restart'", 'modify-instance-metadata-options'):
            self.assertNotIn(text, remote)
        self.assertIn("status.stdout.strip() in ('inactive', 'failed')", remote)
        self.assertIn('json.loads(old_cw) ==', remote)

    def test_backup_precedes_write_and_existing_scrapes_preserved(self):
        remote = module.build_remote()
        self.assertLess(remote.index("backup / 'cloudwatch-config.yml'"), remote.index('config_path.write_bytes('))
        self.assertIn('same_inode(old)', remote)
        self.assertIn('NEW_CLOUDWATCH_COLLECTOR_STOPPED_FILES_PRESERVED', remote)
        self.assertIn("len(nodes) == 3", remote)


if __name__ == '__main__':
    unittest.main()
