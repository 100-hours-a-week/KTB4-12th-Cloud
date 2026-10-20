"""k6 inspect/입력 거절만 실행. HTTP 요청·AWS 호출·실제 부하 실행 없음."""
import json
import os
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = "load-tests/k6/workloads/ecs-deployment-readonly.js"


class ReadonlyLoadTests(unittest.TestCase):
    def inspect(self, profile="smoke", url="https://v2-staging.seonjalal.com", run=None):
        return subprocess.run([
            "k6", "inspect", "-e", "BASE_URL=" + url, "-e", "PROFILE=" + profile,
            "-e", "ECS_RUN_ID=" + (run or f"20261017T000000Z-ecs-readonly-{profile}-1"), SCRIPT,
        ], cwd=ROOT, capture_output=True, text=True)

    def test_smoke_is_one_read_with_tls_verification(self):
        result = self.inspect()
        self.assertEqual(result.returncode, 0, result.stderr)
        options = json.loads(result.stdout)
        self.assertEqual(options["scenarios"]["reads"]["iterations"], 1)
        self.assertFalse(options["insecureSkipTLSVerify"])

    def test_baseline_is_twenty_minutes_at_one_per_second(self):
        result = self.inspect("baseline")
        self.assertEqual(result.returncode, 0, result.stderr)
        read = json.loads(result.stdout)["scenarios"]["reads"]
        self.assertEqual((read["rate"], read["timeUnit"], read["duration"]), (1, "1s", "20m0s"))

    def test_wrong_url_and_run_are_blocked_before_execution(self):
        self.assertNotEqual(self.inspect(url="https://www.seonjalal.com").returncode, 0)
        self.assertNotEqual(self.inspect(run="invalid").returncode, 0)

    def test_existing_authenticated_workload_still_requires_token(self):
        env = os.environ.copy()
        for key in ("ACCESS_TOKEN", "ACCESS_TOKENS", "ACCESS_TOKENS_FILE", "JWT_SECRET_BASE64"):
            env.pop(key, None)
        env["BASE_URL"] = "https://v1-benchmark.seonjalal.com"
        result = subprocess.run([
            "bash", "scripts/run-k6-with-artifacts.sh", "20261017T000000Z-unit-auth-guard", "smoke",
            "load-tests/k6/workloads/migration-readonly-cutover.js",
        ], cwd=ROOT, env=env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("is required", result.stderr)

    def test_wrapper_rejects_non_staging_url_for_readonly_exception(self):
        env = {**os.environ, "BASE_URL": "https://www.seonjalal.com"}
        result = subprocess.run([
            "bash", "scripts/run-k6-with-artifacts.sh", "20261017T000000Z-unit-url-guard", "smoke", SCRIPT,
        ], cwd=ROOT, env=env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("staging URL required", result.stderr)


if __name__ == "__main__":
    unittest.main()
