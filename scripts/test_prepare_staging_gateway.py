"""로컬 SSM 요청 생성 계약 검사. 원격 스크립트는 실행하지 않는다."""
import importlib.util
from pathlib import Path
import subprocess
import unittest

path = Path(__file__).with_name("prepare-staging-gateway.py")
spec = importlib.util.spec_from_file_location("prepare_gateway", path)
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


class PrepareGatewayTest(unittest.TestCase):
    def script(self):
        return prepare.build_script({"compose.yaml": b"local synthetic", "nginx.conf": b"local synthetic"})

    def test_generated_shell_syntax(self):
        result = subprocess.run(["bash", "-n"], input=self.script(), text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_preflight_before_first_write(self):
        script = self.script()
        mutation = script.index('mkdir "$target"')
        for guard in ('test "$actual_instance"', 'test ! -e "$target"', 'ss -H -lnt', 'test "$available_kib" -ge', 'serve_before='):
            self.assertLess(script.index(guard), mutation)

    def test_only_new_project_stopped_on_failure(self):
        script = self.script()
        self.assertIn('if [ "$started" = 1 ]', script)
        self.assertIn('stop gateway', script)
        for dangerous in ("docker compose down", "systemctl restart", "tailscale serve --", "rm -rf", "aws secretsmanager"):
            self.assertNotIn(dangerous, script)

    def test_no_image_build_or_existing_directory_overwrite(self):
        script = self.script()
        self.assertIn('--no-build --pull never', script)
        self.assertIn('mkdir "$target"', script)
        self.assertNotIn('mkdir -p "$target"', script)


if __name__ == "__main__":
    unittest.main()
