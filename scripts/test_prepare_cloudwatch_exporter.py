"""실제 HTTP/AWS/Docker 없이 CloudWatch 설치 계약 검사."""
import ast
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('cloudwatch', Path(__file__).with_name('prepare-cloudwatch-exporter.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class CloudWatchContract(unittest.TestCase):
    def test_python_and_target(self):
        ast.parse(module.REMOTE)
        self.assertEqual(module.build_request()['InstanceIds'], ['i-0c6a75f630436591c'])

    def test_config_is_static_and_single_region(self):
        config = module.configuration()
        self.assertNotIn('discovery', config)
        self.assertEqual(len(config['static']), 6)
        self.assertEqual({j['namespace'] for j in config['static']}, {'AWS/RDS', 'AWS/EC2', 'AWS/ApplicationELB'})
        for job in config['static']:
            self.assertEqual(job['regions'], ['ap-northeast-2'])
            self.assertEqual(len(job['dimensions']), 1)
        self.assertEqual(config['static'][1]['dimensions'][0]['value'], module.ALB)

    def test_no_missing_data_fabrication_and_time_preserved(self):
        for job in module.configuration()['static']:
            for metric in job['metrics']:
                self.assertFalse(metric['nilToZero'])
                self.assertTrue(metric['addCloudwatchTimestamp'])
                self.assertNotIn('delay', metric)
                self.assertIn(metric['period'], (60, 300))

    def test_static_panic_regression_no_percentile_only_requests(self):
        basic = {'Average', 'Minimum', 'Maximum', 'Sum', 'SampleCount'}
        for job in module.configuration()['static']:
            for metric in job['metrics']:
                self.assertTrue(metric['statistics'])
                self.assertTrue(set(metric['statistics']) <= basic)
        response_metrics = [m for m in module.configuration()['static'][1]['metrics']
                            if m['name'] == 'TargetResponseTime']
        self.assertEqual(len(response_metrics), 1)
        self.assertEqual(response_metrics[0]['statistics'], ['Average', 'Maximum'])

    def test_dashboard_does_not_claim_cloudwatch_p95_or_ignored_delay(self):
        self.assertNotIn("'TargetResponseTime', 'p95'", module.REMOTE)
        self.assertIn('NOT p95', module.REMOTE)
        self.assertIn('Average exports are 15m-window aggregates', module.REMOTE)

    def test_lock_and_release_checks_precede_install(self):
        remote = module.REMOTE
        self.assertLess(remote.index('fcntl.flock('), remote.index('root.mkdir('))
        self.assertLess(remote.index('hashlib.sha256(payload)'), remote.index('root.mkdir('))
        self.assertNotIn('archive.extractall(', remote)
        self.assertIn('archive.extractfile(', remote)

    def test_credentials_not_stored_or_hop_changed(self):
        for value in ('AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'password_file', 'GF_SECURITY_ADMIN',
                      'modify-instance-metadata-options', 'metadata_options'):
            self.assertNotIn(value, module.REMOTE)
        self.assertIn('User=seonjalal-cloudwatch', module.REMOTE)
        self.assertIn('--disable-account-alias-lookup', module.REMOTE)

    def test_private_bridge_and_quota(self):
        for value in ("network['Driver'] == 'bridge'", "ipaddress.ip_address(gateway).is_private",
                      "' --listen-address=' + gateway", 'MemoryMax=192M', 'CPUQuota=25%',
                      '--scraping-interval=60', '--cloudwatch-concurrency=2'):
            self.assertIn(value, module.REMOTE)
        self.assertNotIn('0.0.0.0:5000', module.REMOTE)

    def test_host_config_preserved_and_safe_reload(self):
        self.assertIn("{'prometheus', 'node_exporter'}", module.REMOTE)
        self.assertIn("prom_path.open('r+b')", module.REMOTE)
        self.assertIn('same_inode(old)', module.REMOTE)
        self.assertIn("'--signal=HUP'", module.REMOTE)
        self.assertNotIn("'restart'", module.REMOTE)
        self.assertIn('NEW_CLOUDWATCH_COLLECTOR_STOPPED_FILES_PRESERVED', module.REMOTE)

    def test_real_data_gate_not_only_http(self):
        self.assertIn('math.isfinite(value)', module.REMOTE)
        self.assertIn("'awsrdscpuutilizationaverage' in values", module.REMOTE)
        self.assertIn("'awsec2cpucreditbalanceaverage' in values", module.REMOTE)

    def test_does_not_enable_paid_monitoring_options(self):
        for value in ('update-cluster-settings', 'modify-db-instance', 'PutMetricData', 'create_alarm'):
            self.assertNotIn(value, module.REMOTE)
        self.assertIn('CloudWatch API charges apply', module.REMOTE)


if __name__ == '__main__':
    unittest.main()
