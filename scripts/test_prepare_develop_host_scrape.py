"""Develop 대상 추가의 비파괴 계약 검사. 원격 실행 없음."""
import ast
import copy
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('scrape', Path(__file__).with_name('prepare-develop-host-scrape.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
namespace = {}
exec(module.TRANSFORM, namespace)


class DevelopScrapeTests(unittest.TestCase):
    def config(self):
        return {'global': {'scrape_interval': '15s'}, 'scrape_configs': [
            {'job_name': 'prometheus', 'static_configs': [{'targets': ['prometheus:9090']}]},
            {'job_name': 'node_exporter', 'sample_limit': 5000, 'static_configs': copy.deepcopy(module.base.HOSTS)},
            {'job_name': 'cloudwatch', 'scrape_interval': '60s', 'static_configs': [{'targets': ['bridge:5000']}]}]}

    def test_preserves_all_other_settings(self):
        original = self.config()
        saved = copy.deepcopy(original)
        result = namespace['add_develop'](original)
        self.assertEqual(original, saved)
        new = result['scrape_configs'][1]['static_configs'].pop()
        self.assertEqual(new['targets'], ['10.20.0.28:9100'])
        self.assertEqual(result, saved)

    def test_duplicate_target_rejected(self):
        result = namespace['add_develop'](self.config())
        with self.assertRaises(ValueError):
            namespace['add_develop'](result)

    def test_changed_existing_targets_rejected(self):
        config = self.config()
        config['scrape_configs'][1]['static_configs'][0]['targets'] = ['other:9100']
        with self.assertRaises(ValueError):
            namespace['add_develop'](config)

    def test_missing_cloudwatch_rejected(self):
        config = self.config()
        config['scrape_configs'].pop()
        with self.assertRaises(ValueError):
            namespace['add_develop'](config)

    def test_no_grafana_restart_and_rollback(self):
        ast.parse(module.REMOTE)
        self.assertNotIn("docker('restart'", module.REMOTE)
        self.assertNotIn('admin-password', module.REMOTE)
        self.assertIn('write_same_inode(old)', module.REMOTE)
        self.assertIn('len(nodes) == 4', module.REMOTE)
        self.assertIn("config.read_bytes() == old", module.REMOTE)

    def test_only_monitor_instance(self):
        request = module.build_request()
        self.assertEqual(request['InstanceIds'], ['i-0c6a75f630436591c'])


if __name__ == '__main__':
    unittest.main()
