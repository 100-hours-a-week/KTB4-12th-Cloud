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
    def run_loader(enabled, empty=False, extra=None):
        deploy = (RUNTIME / "scripts/deploy.sh").read_text()
        code = "import json" + deploy.split("import json", 1)[1].split("\n' >", 1)[0]
        keys = (
            "DB_PASSWORD", "DB_ROOT_PASSWORD", "JWT_SECRET_BASE64",
            "LOGIN_RATE_LIMIT_HMAC_SECRET_BASE64", "PROFILING_SERVICE_TOKEN",
            "PAYMENT_SERVER_API_KEY",
        )
        return subprocess.run(
            ["python3", "-c", code], input="" if empty else json.dumps(
                {**{key: "test-only" for key in keys}, **(extra or {})}
            ), text=True, capture_output=True,
            env={**os.environ, "AI_CHAT_ENABLED": str(enabled).lower()},
        )


if __name__ == "__main__":
    unittest.main()
