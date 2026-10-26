"""V2 k6 시나리오를 로컬 모의 서버로 검증한다. AWS와 실제 스테이징에는 연결하지 않는다.

k6와 node가 없으면 건너뛴다. 가짜 비밀·가짜 게이트 토큰만 사용한다.
"""
import base64
import contextlib
import json
import os
import pathlib
import shutil
import subprocess
import unittest
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent / "load-tests" / "k6"
SCENARIOS = {
    "credits-balance": "/credits/balance",
    "preferences": "/preferences",
    "dislike-categories": "/preferences/dislike-categories",
    "products-search": "/products",
    "notifications-list": "/notifications",
    "notifications-unread": "/notifications/unread-count",
    "friend-requests-received": "/friend-requests/received",
    "friend-requests-sent": "/friend-requests/sent",
}
SECRET = base64.b64encode(os.urandom(32)).decode()
OTHER_SECRET = base64.b64encode(os.urandom(32)).decode()
GATE = "gate-" + base64.urlsafe_b64encode(os.urandom(18)).decode()
PASSWORD = "Mock1234!"
ISSUER = "mock-issuer"
TOOLS = shutil.which("k6") and shutil.which("node")


@contextlib.contextmanager
def mock_server(**overrides):
    env = {**os.environ, "MOCK_JWT_SECRET_BASE64": SECRET, "MOCK_ISSUER": ISSUER, "MOCK_GATE_TOKEN": GATE,
           "MOCK_PASSWORD": PASSWORD, **overrides}
    process = subprocess.Popen(["node", str(ROOT / "v2" / "mock-server.mjs")], env=env, stdout=subprocess.PIPE, text=True)
    try:
        port = int(process.stdout.readline().strip().split("=")[1])
        yield f"http://127.0.0.1:{port}"
    finally:
        process.terminate()
        process.wait(timeout=5)
        process.stdout.close()


def stats(base):
    with urllib.request.urlopen(base + "/__stats", timeout=5) as response:
        return json.load(response)


def k6_env(base, **overrides):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("STAGING_", "V2_", "ALLOW_", "PROFILE", "BASE_URL"))}
    env.update({
        "BASE_URL": base, "PROFILE": "smoke", "V2_ALLOW_LOCAL_MOCK": "true", "ALLOW_DIRECT_JWT_STAGING": "true",
        "STAGING_JWT_SECRET_BASE64": SECRET, "STAGING_JWT_ISSUER": ISSUER, "STAGING_GATE_TOKEN": GATE,
        "V2_USER_ID_OFFSET": "1000", "V2_USER_COUNT": "10000", "LOADTEST_PASSWORD": PASSWORD,
    })
    env.update({k: v for k, v in overrides.items() if v is not None})
    for key in [k for k, v in overrides.items() if v is None]:
        env.pop(key, None)
    return env


def run_k6(script, base, **overrides):
    return subprocess.run(["k6", "run", "--quiet", "--no-usage-report", str(ROOT / script)], cwd=ROOT,
                          env=k6_env(base, **overrides), capture_output=True, text=True, timeout=120)


@unittest.skipUnless(TOOLS, "k6와 node가 필요하다")
class ScenarioTests(unittest.TestCase):
    def test_each_scenario_passes_smoke_and_hits_only_its_endpoint(self):
        for name, path in SCENARIOS.items():
            with mock_server() as base:
                result = run_k6(f"v2/{name}.js", base)
                self.assertEqual(result.returncode, 0, name + result.stdout[-800:] + result.stderr[-800:])
                seen = stats(base)
                self.assertEqual(seen["hits"], {path: 3}, name)
                self.assertGreaterEqual(len(seen["subjects"]), 3, name)

    def test_tokens_use_offset_plus_number(self):
        with mock_server() as base:
            run_k6("v2/credits-balance.js", base, V2_USER_ID_OFFSET="1000")
            self.assertEqual(sorted(stats(base)["subjects"]), ["1001", "10459", "5730"])

    def test_mixed_smoke_hits_every_read_endpoint_once(self):
        with mock_server() as base:
            result = run_k6("workloads/v2-read-mixed.js", base)
            self.assertEqual(result.returncode, 0, result.stdout[-800:] + result.stderr[-800:])
            self.assertEqual(stats(base)["hits"], {path: 1 for path in SCENARIOS.values()})

    def test_search_uses_korean_keywords_url_encoded(self):
        with mock_server() as base:
            self.assertEqual(run_k6("v2/products-search.js", base).returncode, 0)

    def test_no_secret_or_token_is_printed(self):
        with mock_server() as base:
            result = run_k6("workloads/v2-read-mixed.js", base)
            everything = result.stdout + result.stderr
            for leaked in (SECRET, GATE, "eyJ"):
                self.assertNotIn(leaked, everything)


@unittest.skipUnless(TOOLS, "k6와 node가 필요하다")
class GuardTests(unittest.TestCase):
    def assert_blocked(self, message, **overrides):
        with mock_server() as base:
            target = overrides.pop("BASE_URL", base)
            result = run_k6("v2/credits-balance.js", target, **overrides)
            self.assertNotEqual(result.returncode, 0)
            output = result.stdout + result.stderr
            self.assertIn(message, output)
            for leaked in (SECRET, GATE):
                self.assertNotIn(leaked, output)
            self.assertEqual(stats(base)["hits"], {})

    def test_production_is_blocked(self):
        self.assert_blocked("run only against", BASE_URL="https://www.seonjalal.com")

    def test_other_hosts_are_blocked(self):
        self.assert_blocked("run only against", BASE_URL="https://example.com")

    def test_local_mock_needs_the_explicit_flag(self):
        self.assert_blocked("run only against", V2_ALLOW_LOCAL_MOCK="false")

    def test_direct_jwt_needs_opt_in(self):
        self.assert_blocked("ALLOW_DIRECT_JWT_STAGING", ALLOW_DIRECT_JWT_STAGING="false")

    def test_secret_issuer_gate_and_offset_are_required(self):
        self.assert_blocked("STAGING_JWT_SECRET_BASE64", STAGING_JWT_SECRET_BASE64=None)
        self.assert_blocked("STAGING_JWT_SECRET_BASE64", STAGING_JWT_ISSUER=None)
        self.assert_blocked("STAGING_GATE_TOKEN", STAGING_GATE_TOKEN=None)
        self.assert_blocked("V2_USER_ID_OFFSET", V2_USER_ID_OFFSET=None)

    def test_short_signing_key_is_rejected(self):
        self.assert_blocked("at least 32 bytes", STAGING_JWT_SECRET_BASE64=base64.b64encode(b"short").decode())

    def test_profiles_above_30_rps_need_approval(self):
        self.assert_blocked("exceeds 30 RPS", PROFILE="spike")
        self.assert_blocked("exceeds 30 RPS", PROFILE="breakpoint")
        self.assert_blocked("exceeds 30 RPS", PROFILE="stress")


@unittest.skipUnless(TOOLS, "k6와 node가 필요하다")
class FailureDetectionTests(unittest.TestCase):
    def assert_threshold_failure(self, target_overrides=None, **overrides):
        with mock_server(**(target_overrides or {})) as base:
            result = run_k6("v2/credits-balance.js", base, **overrides)
            self.assertEqual(result.returncode, 99, result.stdout[-600:] + result.stderr[-600:])
            return stats(base)

    def test_wrong_gate_token_fails(self):
        seen = self.assert_threshold_failure(STAGING_GATE_TOKEN="gate-wrong")
        self.assertEqual(seen["hits"], {})
        self.assertGreater(seen["rejected"]["gate"], 0)

    def test_wrong_signing_secret_fails_with_401(self):
        seen = self.assert_threshold_failure(STAGING_JWT_SECRET_BASE64=OTHER_SECRET)
        self.assertGreater(seen["rejected"]["auth"], 0)

    def test_wrong_issuer_fails(self):
        self.assert_threshold_failure(STAGING_JWT_ISSUER="someone-else")

    def test_server_error_fails(self):
        seen = self.assert_threshold_failure(target_overrides={"MOCK_FAIL_PATH": "credits"})
        self.assertEqual(seen["hits"], {"/credits/balance": 3})


@unittest.skipUnless(TOOLS, "k6와 node가 필요하다")
class OptionsTests(unittest.TestCase):
    def inspect(self, script, profile):
        env = k6_env("http://127.0.0.1:1", PROFILE=profile)
        flags = [part for key, value in env.items()
                 if key.startswith(("STAGING_", "V2_", "ALLOW_", "PROFILE", "BASE_URL")) for part in ("-e", f"{key}={value}")]
        result = subprocess.run(["k6", "inspect", *flags, str(ROOT / script)], cwd=ROOT, env=env,
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr[-800:])
        return json.loads(result.stdout)

    def test_mixed_capacity_never_exceeds_30_rps(self):
        options = self.inspect("workloads/v2-read-mixed.js", "capacity")
        stages = options["scenarios"]["v2_read"]["stages"]
        self.assertEqual(max(stage["target"] for stage in stages), 30)

    def test_mixed_baseline_is_5_rps(self):
        options = self.inspect("workloads/v2-read-mixed.js", "baseline")
        self.assertEqual(options["scenarios"]["v2_read"]["rate"], 5)

    def test_mixed_has_abort_thresholds(self):
        options = self.inspect("workloads/v2-read-mixed.js", "baseline")
        names = list(options["thresholds"])
        self.assertIn("http_req_failed{workload:v2_read}", names)
        aborting = [t for t in options["thresholds"]["http_req_failed{workload:v2_read}"]
                    if isinstance(t, dict) and t.get("abortOnFail")]
        self.assertTrue(aborting)

    def test_single_endpoint_capacity_never_exceeds_30_rps(self):
        for name in SCENARIOS:
            options = self.inspect(f"v2/{name}.js", "capacity")
            stages = next(iter(options["scenarios"].values()))["stages"]
            self.assertLessEqual(max(stage["target"] for stage in stages), 30, name)


@unittest.skipUnless(TOOLS, "k6와 node가 필요하다")
class VerifyUserIdTests(unittest.TestCase):
    def run_verify(self, base, **overrides):
        return run_k6("v2/verify-user-ids.js", base, **overrides)

    def test_contiguous_ids_report_the_offset(self):
        with mock_server(MOCK_ID_BASE="2000") as base:
            result = self.run_verify(base)
            self.assertEqual(result.returncode, 0, result.stdout[-600:] + result.stderr[-600:])
            self.assertRegex(result.stderr + result.stdout, r'V2_USER_ID_OFFSET\W+2000')
            self.assertEqual(stats(base)["logins"], 3)

    def test_non_contiguous_ids_fail(self):
        with mock_server(MOCK_ID_GAP="7") as base:
            result = self.run_verify(base)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("not contiguous", result.stdout + result.stderr)

    def test_password_is_required_and_never_printed(self):
        with mock_server() as base:
            missing = self.run_verify(base, LOADTEST_PASSWORD=None)
            self.assertNotEqual(missing.returncode, 0)
            self.assertIn("LOADTEST_PASSWORD", missing.stdout + missing.stderr)
            ok = self.run_verify(base)
            self.assertNotIn(PASSWORD, ok.stdout + ok.stderr)

    def test_wrong_password_fails_after_only_three_logins(self):
        with mock_server() as base:
            result = self.run_verify(base, LOADTEST_PASSWORD="Wrong1234!")
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(stats(base)["logins"], 3)

    def test_production_is_blocked(self):
        with mock_server() as base:
            result = self.run_verify(base, BASE_URL="https://www.seonjalal.com")
            self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
