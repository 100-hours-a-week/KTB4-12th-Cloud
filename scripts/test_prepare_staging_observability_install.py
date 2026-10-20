"""합성 계약 검사만 수행. AWS/Docker/원격 호출 없음."""
import ast
import base64
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('install', Path(__file__).with_name('prepare-staging-observability-install.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class InstallContract(unittest.TestCase):
    def test_exclusive_lock_precedes_install_preflight(self):
        self.assertIn('fcntl.LOCK_EX | fcntl.LOCK_NB', module.REMOTE)
        self.assertLess(module.REMOTE.index('fcntl.flock('), module.REMOTE.index('not root.exists()'))
        self.assertLess(module.REMOTE.index('fcntl.flock('), module.REMOTE.index('docker("pull"'))

    def test_remote_python_parses(self):
        ast.parse(module.REMOTE)

    def test_exact_instance(self):
        self.assertEqual(module.build_request()['InstanceIds'], ['i-0c6a75f630436591c'])
        self.assertIn('identity.get("accountId")', module.REMOTE)

    def test_payload_roundtrip(self):
        command = module.build_request()['Parameters']['commands'][0]
        tree = ast.parse(command.split("\n", 1)[1].rsplit('\nPY', 1)[0])
        call = tree.body[1].value.args[0].args[0]
        remote = base64.b64decode(ast.literal_eval(call.args[0])).decode()
        self.assertIn(module.PROMETHEUS, remote)
        self.assertIn(module.GRAFANA, remote)
        self.assertNotIn('__PROMETHEUS__', remote)

    def test_fixed_versions_and_digest_execution(self):
        self.assertNotIn(':latest', module.REMOTE)
        self.assertIn('images["grafana"]["digest"]', module.REMOTE)
        self.assertIn('images["prometheus"]["digest"]', module.REMOTE)

    def test_localhost_only(self):
        self.assertIn('127.0.0.1:3000:3000', module.REMOTE)
        self.assertIn('127.0.0.1:9090:9090', module.REMOTE)
        self.assertNotIn('0.0.0.0:', module.REMOTE)

    def test_no_secret_value_in_env_or_output(self):
        self.assertIn('GF_SECURITY_ADMIN_PASSWORD__FILE=', module.REMOTE)
        self.assertNotIn('GF_SECURITY_ADMIN_PASSWORD=', module.REMOTE)
        self.assertIn('os.chmod(password_file, 0o400)', module.REMOTE)
        self.assertIn('secrets.token_urlsafe(36)', module.REMOTE)
        self.assertNotIn('print(auth)', module.REMOTE)

    def test_resource_limits_and_retention(self):
        for value in ('640m', '384m', '--storage.tsdb.retention.time=3d',
                      '--storage.tsdb.retention.size=5GB', 'max-size=10m', 'no-new-privileges:true'):
            self.assertIn(value, module.REMOTE)

    def test_no_destructive_or_privileged_docker_operation(self):
        for value in ('"--privileged"', '"--network", "host"', '"/var/run/docker.sock',
                      '"system", "prune"', '"volume", "rm"', 'shutil.rmtree'):
            self.assertNotIn(value, module.REMOTE)
        self.assertIn('not root.exists()', module.REMOTE)
        self.assertIn('not existing', module.REMOTE)

    def test_health_and_scope(self):
        self.assertIn('GRAFANA_PROMETHEUS_DATASOURCE_OK', module.REMOTE)
        self.assertIn('item.get("health") == "up"', module.REMOTE)
        self.assertIn('metrics and dashboard verification pending', module.REMOTE)


if __name__ == '__main__':
    unittest.main()
