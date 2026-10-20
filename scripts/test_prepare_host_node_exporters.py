"""원격 실행 없는 exporter 설치 계약 검사."""
import ast
import base64
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('exporters', Path(__file__).with_name('prepare-host-node-exporters.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ExporterContract(unittest.TestCase):
    def test_develop_target_is_isolated(self):
        request = module.build_request('develop')
        self.assertEqual(request['InstanceIds'], ['i-06f8ccdce3a1e9ff4'])
        remote = module.REMOTE.replace('__TARGETS__', repr(module.DEVELOP_TARGETS)).replace('__VERSION__', module.VERSION)
        compile(remote, '<develop-exporter>', 'exec')
        self.assertIn('10.20.0.28', remote)
        for instance in module.TARGETS:
            self.assertNotIn(instance, remote)
        # 요청에 담긴 실제 원격 코드 역시 선택된 대상만 포함하는지 확인.
        self.assertIn(base64.b64encode(remote.encode()).decode(), request['Parameters']['commands'][0])

    def test_unknown_target_rejected(self):
        with self.assertRaises(ValueError):
            module.build_request('production')

    def test_syntax_and_target_scope(self):
        ast.parse(module.REMOTE)
        request = module.build_request()
        self.assertEqual(len(request['InstanceIds']), 3)
        self.assertNotIn('i-0417358a95b551c12', request['InstanceIds'])
        self.assertEqual(request['MaxConcurrency'], '1')
        self.assertEqual(request['MaxErrors'], '0')

    def test_identity_and_lock_before_writes(self):
        remote = module.REMOTE
        self.assertIn("identity.get('privateIp') == ip", remote)
        self.assertLess(remote.index('fcntl.flock('), remote.index('root.mkdir('))
        self.assertLess(remote.index("'허용된 비운영 대상 아님'"), remote.index('root.mkdir('))

    def test_verified_single_archive_member(self):
        remote = module.REMOTE
        self.assertIn('hashlib.sha256(archive).hexdigest() == matches[0]', remote)
        self.assertIn('package.extractfile(member)', remote)
        self.assertNotIn('package.extractall(', remote)
        self.assertLess(remote.index('hashlib.sha256(archive)'), remote.index('root.mkdir('))

    def test_no_application_or_docker_commands(self):
        remote = module.REMOTE
        self.assertNotIn("run('docker'", remote)
        self.assertNotIn("run('mysql'", remote)
        self.assertNotIn("run('systemctl', 'restart'", remote)
        self.assertNotIn("run('systemctl', 'stop'", remote)

    def test_private_binding_and_nonroot_service(self):
        self.assertIn("'--web.listen-address=' + ip + ':9100'", module.REMOTE)
        self.assertNotIn('0.0.0.0:9100', module.REMOTE)
        self.assertIn('User=seonjalal-node-exporter', module.REMOTE)
        self.assertIn('NoNewPrivileges=true', module.REMOTE)

    def test_limits_and_no_file_overwrite(self):
        for value in ('MemoryMax=128M', 'CPUQuota=20%', 'not root.exists()', 'not unit.exists()',
                      'collector.disable-defaults', 'Prometheus reachability and scraping pending'):
            self.assertIn(value, module.REMOTE)


if __name__ == '__main__':
    unittest.main()
