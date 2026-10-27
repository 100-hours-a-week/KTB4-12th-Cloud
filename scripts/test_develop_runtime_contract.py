"""Develop 필수 설정 누락 시 실제 배포 전에 거절하는 로컬 회귀 검사."""

from pathlib import Path
import re
import json
import os
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "operations/develop/runtime"
PUBLIC_KEYS = (
    "PAYMENT_SERVER_BASE_URL", "AI_CHAT_BASE_URL",
    "AI_CHAT_CONNECT_TIMEOUT", "AI_CHAT_READ_TIMEOUT",
    "AI_CHAT_SSE_IDLE_TIMEOUT", "AI_CHAT_SSE_TOTAL_TIMEOUT",
    "AI_CHAT_SSE_MAX_CONNECTIONS", "CHAT_CONVERSATION_TOKEN_ISSUER",
)
SECRET_KEYS = (
    "PAYMENT_SERVER_API_KEY", "AI_CHAT_SERVICE_TOKEN",
    "CHAT_CONVERSATION_TOKEN_SECRET_BASE64",
    "REDIS_PASSWORD", "SMTP_USERNAME", "SMTP_PASSWORD",
)
# 항상 필요한 Runtime Secret(AI 설정과 무관). 테스트의 가짜 Secret 응답에 포함한다.
BASE_SECRET_KEYS = (
    "DB_PASSWORD", "DB_ROOT_PASSWORD", "JWT_SECRET_BASE64",
    "LOGIN_RATE_LIMIT_HMAC_SECRET_BASE64", "PROFILING_SERVICE_TOKEN",
    "PAYMENT_SERVER_API_KEY", "REDIS_PASSWORD", "SMTP_USERNAME", "SMTP_PASSWORD",
)


class DevelopRuntimeContractTest(unittest.TestCase):
    def test_compose_wires_all_new_required_settings(self):
        text = (RUNTIME / "compose.yaml").read_text()
        for key in PUBLIC_KEYS + SECRET_KEYS:
            self.assertIn(f"{key}: ${{{key}:", text, key)
        self.assertIn("AI_CHAT_ENABLED: ${AI_CHAT_ENABLED:-true}", text)
        payment = text.split("\n  payment-server:\n", 1)[1].split(
            "\n  database:\n", 1)[0]
        for key in ("DB_URL", "DB_USERNAME", "DB_PASSWORD", "PAYMENT_SERVER_API_KEY"):
            self.assertRegex(payment, rf"(?m)^      {key}:")

    def test_secrets_only_declared_in_secret_loader(self):
        deploy = (RUNTIME / "scripts/deploy.sh").read_text()
        loader = deploy.split("import json", 1)[1]
        example = (RUNTIME / ".env.example").read_text()
        for key in SECRET_KEYS:
            self.assertIn(f'"{key}"', loader)
            self.assertNotRegex(example, rf"(?m)^{key}=")

    def test_missing_public_settings_rejected_before_external_calls(self):
        script = RUNTIME / "scripts/deploy.sh"
        # 각 누락 설정이 외부 AWS/Docker 호출 이전에 거절되는지 확인한다.
        declared = re.search(r"required_keys=\((.*?)\)", script.read_text(), re.S)
        keys = declared.group(1).split() + list(PUBLIC_KEYS[1:])
        for missing in PUBLIC_KEYS:
            with self.subTest(key=missing), tempfile.TemporaryDirectory() as folder:
                target = Path(folder)
                (target / "scripts").mkdir()
                (target / "nginx").mkdir()
                for relative in ("compose.yaml", "nginx/default.conf", "scripts/verify.sh"):
                    (target / relative).touch()
                (target / ".env").write_text("".join(
                    f"{key}=local-test\n" for key in keys if key != missing
                ))
                result = subprocess.run(
                    ["bash", str(script), folder], text=True, capture_output=True,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("필수 환경변수가 없거나 비어 있음: " + missing, result.stderr)

    def test_disabled_loader_does_not_require_ai_secrets(self):
        result = self.run_loader(False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PAYMENT_SERVER_API_KEY=", result.stdout)
        self.assertNotIn("AI_CHAT_SERVICE_TOKEN=", result.stdout)
        self.assertNotIn("CHAT_CONVERSATION_TOKEN_SECRET_BASE64=", result.stdout)

    def test_enabled_loader_rejects_missing_ai_secrets(self):
        result = self.run_loader(True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("AI_CHAT_SERVICE_TOKEN", result.stderr)
        self.assertNotIn("test-only", result.stderr)

    def test_empty_secret_response_has_explicit_error_without_traceback(self):
        result = self.run_loader(False, empty=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Runtime Secret 조회 결과가 없어", result.stderr)
        self.assertNotIn("Traceback", result.stderr)

    def test_disabled_public_guard_skips_ai_keys_but_still_checks_images(self):
        result = self.run_public_guard("false")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("허용되지 않은 ECR Image 경로", result.stderr)
        self.assertNotIn("필수 환경변수", result.stderr)

    def test_invalid_feature_flag_is_rejected_before_external_calls(self):
        result = self.run_public_guard("invalid")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("AI_CHAT_ENABLED는 true 또는 false여야 함", result.stderr)

    def test_search_is_pinned_off_and_static_aws_keys_are_not_forwarded(self):
        compose = (RUNTIME / "compose.yaml").read_text()
        self.assertIn('PRODUCT_SEARCH_ENABLED: "false"', compose)
        self.assertNotIn("AWS_ACCESS_KEY_ID:", compose)
        self.assertNotIn("AWS_SECRET_ACCESS_KEY:", compose)

    def test_search_override_rejected_before_external_calls(self):
        for value in ("true", "TRUE", "invalid"):
            with self.subTest(value=value):
                result = self.run_public_guard("false", search=value)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("PRODUCT_SEARCH_ENABLED", result.stderr)

    def test_existing_database_not_recreated_during_app_deploy(self):
        deploy = (RUNTIME / "scripts/deploy.sh").read_text()
        self.assertIn('ps --all --quiet database', deploy)
        self.assertIn('if [[ -n "${database_id}" ]]', deploy)
        self.assertIn('pull main-backend payment-server frontend', deploy)
        self.assertIn('for application_service in main-backend payment-server frontend', deploy)
        self.assertIn('--no-deps --wait --wait-timeout 240 "${application_service}"', deploy)
        self.assertNotIn('--remove-orphans', deploy)

    def test_payment_waits_for_migration_owner(self):
        compose = (RUNTIME / "compose.yaml").read_text()
        payment = compose.split("\n  payment-server:\n", 1)[1].split("\n  database:\n", 1)[0]
        self.assertRegex(payment, r"depends_on:\s+main-backend:\s+condition: service_healthy")

    def test_supplied_optional_configuration_is_wired(self):
        compose = (RUNTIME / "compose.yaml").read_text()
        for key in ("PAYMENT_CONNECT_TIMEOUT", "PAYMENT_COMMAND_TIMEOUT", "PAYMENT_QUERY_TIMEOUT",
                    "AI_PROFILE_CONNECT_TIMEOUT", "AI_PROFILE_READ_TIMEOUT", "AI_PROFILE_BATCH_SIZE",
                    "AI_PROFILE_RECOVERY_BATCH_SIZE", "AI_PROFILE_DISPATCH_INTERVAL",
                    "DISCORD_WEBHOOK_URL", "GOOGLE_SERVICE_ACCOUNT_JSON_BASE64", "GOOGLE_SHEET_ID", "GOOGLE_SHEET_TAB"):
            self.assertIn(key + ": ${" + key + ":-", compose)

    def test_optional_secrets_allowed_but_arbitrary_keys_ignored(self):
        result = self.run_loader(False, extra={
            "DISCORD_WEBHOOK_URL": "https://example.invalid/test-only",
            "GOOGLE_SERVICE_ACCOUNT_JSON_BASE64": "dGVzdC1vbmx5",
            "AWS_ACCESS_KEY_ID": "test-only-not-forwarded",
            "PRODUCT_SEARCH_ENABLED": "true",
        })
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("DISCORD_WEBHOOK_URL=", result.stdout)
        self.assertIn("GOOGLE_SERVICE_ACCOUNT_JSON_BASE64=", result.stdout)
        self.assertNotIn("AWS_ACCESS_KEY_ID=", result.stdout)
        self.assertNotIn("PRODUCT_SEARCH_ENABLED=", result.stdout)

    def test_optional_secret_unsafe_or_non_string_rejected_without_value(self):
        for value in ("test-only\nunsafe", ["test-only"], 123):
            with self.subTest(value=value):
                result = self.run_loader(False, extra={"DISCORD_WEBHOOK_URL": value})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("DISCORD_WEBHOOK_URL", result.stderr)
                self.assertNotIn("test-only", result.stderr)

    def test_redis_runs_inside_the_network_and_backend_waits_for_it(self):
        compose = (RUNTIME / "compose.yaml").read_text()
        redis = compose.split("\n  redis:\n", 1)[1].split("\n  database:\n", 1)[0]
        self.assertIn("image: redis:7.4-alpine", redis)
        self.assertIn('--requirepass "$$REDIS_PASSWORD"', redis)
        self.assertNotRegex(redis, r"(?m)^    ports:")  # 호스트 Port 비공개
        self.assertIn("healthcheck:", redis)
        self.assertRegex(redis, r"(?m)^    mem_limit: ")
        backend = compose.split("\n  main-backend:\n", 1)[1].split("\n  payment-server:\n", 1)[0]
        self.assertRegex(backend, r"depends_on:(?:.|\n)*redis:\s+condition: service_healthy")
        for line in ('REDIS_HOST: redis', 'REDIS_PORT: "6379"', "REDIS_PASSWORD: ${REDIS_PASSWORD:?"):
            self.assertIn(line, backend)

    def test_smtp_uses_ses_with_fixed_transport_and_secret_credentials(self):
        compose = (RUNTIME / "compose.yaml").read_text()
        backend = compose.split("\n  main-backend:\n", 1)[1].split("\n  payment-server:\n", 1)[0]
        for line in ('SMTP_PORT: "587"', 'SMTP_AUTH: "true"', 'SMTP_STARTTLS_ENABLE: "true"',
                     "SMTP_FROM_ADDRESS: no-reply@seonjalal.com",
                     "SMTP_USERNAME: ${SMTP_USERNAME:?", "SMTP_PASSWORD: ${SMTP_PASSWORD:?"):
            self.assertIn(line, backend)
        self.assertIn("email-smtp.${AWS_REGION:-ap-northeast-2}.amazonaws.com", backend)
        # 메일 서버 장애가 앱 전체 Health를 내리지 않도록 메일만 Health에서 제외한다.
        self.assertIn('MANAGEMENT_HEALTH_MAIL_ENABLED: "false"', backend)

    def test_redis_and_smtp_secrets_are_required_by_the_loader(self):
        for key in ("REDIS_PASSWORD", "SMTP_USERNAME", "SMTP_PASSWORD"):
            with self.subTest(key=key):
                result = self.run_loader(False, drop=key)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(key, result.stderr)
                self.assertNotIn("test-only", result.stderr)

    def test_loader_accepts_ses_smtp_password_characters(self):
        # SES SMTP 비밀번호는 Base64라 + / = 를 포함할 수 있다.
        result = self.run_loader(False, extra={"SMTP_PASSWORD": "Ab+c/dE=="})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("SMTP_PASSWORD=Ab+c/dE==", result.stdout)

    def test_deploy_starts_redis_before_apps_and_logs_failed_service(self):
        deploy = (RUNTIME / "scripts/deploy.sh").read_text()
        redis_up = deploy.index("--wait-timeout 120 redis")
        app_pull = deploy.index("pull main-backend payment-server frontend")
        self.assertLess(redis_up, app_pull)
        self.assertIn('logs --no-color --tail 100 "${application_service}"', deploy)
        self.assertIn('exit "${deploy_status}"', deploy)

    def test_verify_checks_redis_health(self):
        verify = (RUNTIME / "scripts/verify.sh").read_text()
        self.assertIn("for service in database redis main-backend payment-server frontend; do", verify)

    @staticmethod
    def run_public_guard(flag, search=None):
        script = RUNTIME / "scripts/deploy.sh"
        declared = re.search(r"required_keys=\((.*?)\)", script.read_text(), re.S)
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)
            (target / "scripts").mkdir()
            (target / "nginx").mkdir()
            for relative in ("compose.yaml", "nginx/default.conf", "scripts/verify.sh"):
                (target / relative).touch()
            # 잘못된 이미지 경로에서 반드시 중단시켜 실제 AWS·sudo 호출을 방지한다.
            (target / ".env").write_text("".join(
                f"{key}=local-test\n" for key in declared.group(1).split()
            ) + f"AI_CHAT_ENABLED={flag}\n" + (f"PRODUCT_SEARCH_ENABLED={search}\n" if search is not None else ""))
            return subprocess.run(
                ["bash", str(script), folder], text=True, capture_output=True,
            )

    @staticmethod
    def run_loader(enabled, empty=False, extra=None, drop=None):
        deploy = (RUNTIME / "scripts/deploy.sh").read_text()
        code = "import json" + deploy.split("import json", 1)[1].split("\n' >", 1)[0]
        return subprocess.run(
            ["python3", "-c", code], input="" if empty else json.dumps(
                {**{key: "test-only" for key in BASE_SECRET_KEYS if key != drop}, **(extra or {})}
            ), text=True, capture_output=True,
            env={**os.environ, "AI_CHAT_ENABLED": str(enabled).lower()},
        )


if __name__ == "__main__":
    unittest.main()
