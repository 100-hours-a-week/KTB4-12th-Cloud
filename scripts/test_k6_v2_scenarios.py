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
            self.assertRegex(result.stderr + result.stdout, r'V2_USER_ID_OFFSET\W+2500')
            self.assertRegex(result.stderr + result.stdout, r'V2_USER_COUNT\W+9500')
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


@unittest.skipUnless(TOOLS, "k6와 node가 필요하다")
class MixedReadWriteTests(unittest.TestCase):
    SCRIPT = "workloads/v2-mixed-rw.js"
    EXPECTED_SMOKE_HITS = {
        "/products/:id": 1, "/users/me": 1, "/friends": 4, "/gifts/received": 1, "/credits/balance": 1,
        "/notifications": 1, "/notifications/unread-count": 1, "/preferences": 1,
        "/preferences/dislike-categories": 1, "/friend-requests/received": 1, "/friend-requests/sent": 1,
        "/payments/:id": 1, "PUT /preferences": 1, "PUT /preferences/dislike-categories": 1,
        "POST /friend-requests": 1, "POST /friend-requests/:id/accept": 1, "POST /payments": 3,
        "POST /gifts/received/:id/accept": 2, "POST /gifts/:id/review": 1, "DELETE /gifts/:id/review": 1,
        "POST /payments/:id/cancellations": 1,
    }

    def test_smoke_runs_every_operation_with_the_right_user(self):
        with mock_server() as base:
            result = run_k6(self.SCRIPT, base)
            self.assertEqual(result.returncode, 0, result.stdout[-1200:] + result.stderr[-800:])
            seen = stats(base)
            for key, count in self.EXPECTED_SMOKE_HITS.items():
                self.assertEqual(seen["hits"].get(key), count, key)
            self.assertEqual(seen["rejected"], {"gate": 0, "auth": 0})

    def test_acceptance_needs_the_recipients_token(self):
        # 모의 서버는 수락을 받는 사람의 토큰이 아니면 404로 거절한다. 스모크가 통과하면 시나리오가 받는 사람 토큰을 쓴 것이다.
        with mock_server() as base:
            result = run_k6(self.SCRIPT, base)
            self.assertEqual(result.returncode, 0)
            seen = stats(base)
            # 선물 수락은 결제·수락 흐름과 결제·수락·리뷰 흐름에서 한 번씩, 친구 수락은 친구 흐름에서 한 번이다.
            self.assertEqual(seen["hits"]["POST /gifts/received/:id/accept"], 2)
            self.assertEqual(seen["hits"]["POST /friend-requests/:id/accept"], 1)
            self.assertEqual(seen["rejected"], {"gate": 0, "auth": 0})

    def test_read_write_ratio_is_about_80_20(self):
        with mock_server() as base:
            result = run_k6(self.SCRIPT, base, PROFILE="mockcheck", V2_MOCKCHECK_ITERATIONS="2000")
            self.assertEqual(result.returncode, 0, result.stdout[-1200:] + result.stderr[-800:])
            seen = stats(base)
            share = seen["writes"] / (seen["reads"] + seen["writes"])
            self.assertGreater(share, 0.17, share)
            self.assertLess(share, 0.23, share)
            self.assertGreater(seen["hits"]["POST /payments"], 60)
            self.assertLess(seen["hits"]["POST /payments"], 180)
            self.assertGreater(seen["hits"]["POST /gifts/:id/review"], 5)

    def test_scenarios_do_not_fail_with_normal_business_errors_outside_smoke(self):
        # 가격이 어긋나 결제가 409로 거절돼도 정상 업무 오류라 본 시험은 통과하지만, 스모크는 계약 오류로 잡아야 한다.
        with mock_server(MOCK_PRICE_SKEW="1") as base:
            smoke = run_k6(self.SCRIPT, base)
            self.assertEqual(smoke.returncode, 99, smoke.stdout[-800:] + smoke.stderr[-800:])
        with mock_server(MOCK_PRICE_SKEW="1") as base:
            mix = run_k6(self.SCRIPT, base, PROFILE="mockcheck", V2_MOCKCHECK_ITERATIONS="600")
            self.assertEqual(mix.returncode, 0, mix.stdout[-800:] + mix.stderr[-800:])

    def test_skipped_flow_fails_the_smoke_but_not_the_mix(self):
        # 친구가 없어 결제 흐름을 건너뛰면 스모크는 실패해야 한다(조용한 통과 방지). 본 시험에서는 건너뜀을 허용한다.
        with mock_server(MOCK_NO_FRIENDS="1") as base:
            smoke = run_k6(self.SCRIPT, base)
            self.assertEqual(smoke.returncode, 99, smoke.stdout[-800:] + smoke.stderr[-800:])
            self.assertIn("flow ran without being skipped", smoke.stdout + smoke.stderr)
        with mock_server(MOCK_NO_FRIENDS="1") as base:
            mix = run_k6(self.SCRIPT, base, PROFILE="mockcheck", V2_MOCKCHECK_ITERATIONS="300")
            self.assertEqual(mix.returncode, 0, mix.stdout[-800:] + mix.stderr[-800:])
            self.assertEqual(stats(base)["hits"].get("POST /payments", 0), 0)

    def test_smoke_can_be_repeated_because_the_friend_request_partner_is_random(self):
        with mock_server() as base:
            for _ in range(6):
                result = run_k6(self.SCRIPT, base)
                self.assertEqual(result.returncode, 0, result.stdout[-600:] + result.stderr[-600:])
            self.assertGreaterEqual(len(set(stats(base)["partners"])), 4)

    def test_fixture_style_gifts_are_invisible_and_reviews_use_paid_gifts(self):
        with mock_server() as base:
            result = run_k6(self.SCRIPT, base, PROFILE="mockcheck", V2_MOCKCHECK_ITERATIONS="800")
            self.assertEqual(result.returncode, 0, result.stdout[-800:] + result.stderr[-800:])
            seen = stats(base)
            self.assertGreater(seen["hits"]["POST /gifts/:id/review"], 0)
            self.assertEqual(seen["hits"]["POST /gifts/:id/review"], seen["hits"]["DELETE /gifts/:id/review"])

    def test_server_error_on_a_write_fails_the_smoke(self):
        with mock_server(MOCK_FAIL_PATH="payments") as base:
            result = run_k6(self.SCRIPT, base)
            self.assertEqual(result.returncode, 99, result.stdout[-800:] + result.stderr[-800:])

    def test_wrong_gate_token_fails_in_setup(self):
        with mock_server() as base:
            result = run_k6(self.SCRIPT, base, STAGING_GATE_TOKEN="gate-wrong")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Product sampling failed", result.stdout + result.stderr)
            self.assertEqual(stats(base)["hits"], {})

    def test_no_secret_is_printed(self):
        with mock_server() as base:
            result = run_k6(self.SCRIPT, base)
            for leaked in (SECRET, GATE, "eyJ"):
                self.assertNotIn(leaked, result.stdout + result.stderr)

    def test_mockcheck_profile_is_blocked_outside_the_mock(self):
        env = k6_env("https://v2-staging.seonjalal.com", PROFILE="mockcheck")
        flags = [part for key, value in env.items()
                 if key.startswith(("STAGING_", "V2_", "ALLOW_", "PROFILE", "BASE_URL")) for part in ("-e", f"{key}={value}")]
        result = subprocess.run(["k6", "inspect", *flags, str(ROOT / self.SCRIPT)], cwd=ROOT, env=env,
                                capture_output=True, text=True, timeout=60)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("mockcheck runs only against the local mock", result.stdout + result.stderr)

    def inspect(self, profile):
        env = k6_env("http://127.0.0.1:1", PROFILE=profile)
        flags = [part for key, value in env.items()
                 if key.startswith(("STAGING_", "V2_", "ALLOW_", "PROFILE", "BASE_URL")) for part in ("-e", f"{key}={value}")]
        result = subprocess.run(["k6", "inspect", *flags, str(ROOT / self.SCRIPT)], cwd=ROOT, env=env,
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr[-800:])
        return json.loads(result.stdout)

    def test_thresholds_judge_reads_and_writes_separately(self):
        thresholds = self.inspect("baseline")["thresholds"]
        self.assertEqual(thresholds["http_req_duration{workload:v2_rw,rw:read}"], ["p(95)<500", "p(99)<1000"])
        self.assertEqual(thresholds["http_req_duration{workload:v2_rw,rw:write}"], ["p(95)<800", "p(99)<1500"])
        self.assertNotIn("http_req_duration{workload:v2_rw}", thresholds)
        aborting = [t for t in thresholds["http_req_failed{workload:v2_rw}"] if isinstance(t, dict) and t.get("abortOnFail")]
        self.assertTrue(aborting)

    def test_capacity_never_exceeds_30_iterations_per_second(self):
        stages = self.inspect("capacity")["scenarios"]["v2_rw"]["stages"]
        self.assertEqual(max(stage["target"] for stage in stages), 30)

    def test_profiles_above_30_need_approval(self):
        with mock_server() as base:
            for profile in ("spike", "breakpoint"):
                result = run_k6(self.SCRIPT, base, PROFILE=profile)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("exceeds 30 RPS", result.stdout + result.stderr)
            self.assertEqual(stats(base)["hits"], {})


if __name__ == "__main__":
    unittest.main()
