"""비밀이 아닌 합성 입력으로 Develop 복구 준비 검증. 외부 연결 없음."""

import base64
import importlib.util
from pathlib import Path
import unittest


spec = importlib.util.spec_from_file_location(
    "recovery", Path(__file__).with_name("prepare-develop-recovery-inputs.py")
)
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


def fixture():
    return dict(zip(recovery.PUBLIC_KEYS, (
        "https://ai.example/v1/chat/conversations", "1s", "15s", "60s", "240s", "2",
    )))


class RecoveryInputsTest(unittest.TestCase):
    def test_local_selection_does_not_import_database_jwt_or_aws_credentials(self):
        supplied = {**fixture(), "AI_CHAT_SERVICE_TOKEN": "test-only-ai-token",
                    "AWS_ACCESS_KEY_ID": "test-only-ignore", "AWS_SECRET_ACCESS_KEY": "test-only-ignore",
                    "DB_PASSWORD": "test-only-ignore", "JWT_SECRET_BASE64": "test-only-ignore"}
        values, token = recovery.select_local_inputs("\n".join(f"{key}={value}" for key, value in supplied.items()))
        self.assertEqual(set(values), set(recovery.PUBLIC_KEYS))
        self.assertEqual(token, "test-only-ai-token")
        result = recovery.prepare_payload(values, token)
        self.assertNotIn("DB_PASSWORD", result["proposed_secrets"])
        self.assertNotIn("JWT_SECRET_BASE64", result["proposed_secrets"])
        self.assertEqual(result["public"]["PRODUCT_SEARCH_ENABLED"], "false")

    def test_duplicate_selected_key_rejected(self):
        content = "\n".join(f"{key}={value}" for key, value in fixture().items())
        with self.assertRaises(ValueError):
            recovery.select_local_inputs(content + "\nAI_CHAT_SERVICE_TOKEN=test-only\nAI_CHAT_SERVICE_TOKEN=other-test")

    def test_prepares_enabled_inputs_and_independent_secret_candidates(self):
        payload = recovery.prepare_payload(fixture(), "test-only")
        self.assertEqual(payload["public"]["AI_CHAT_ENABLED"], "true")
        self.assertEqual(payload["public"]["PAYMENT_SERVER_BASE_URL"], "http://payment-server:8090")
        self.assertEqual(len(payload["proposed_secrets"]), 3)
        self.assertEqual(len(base64.b64decode(
            payload["proposed_secrets"]["CHAT_CONVERSATION_TOKEN_SECRET_BASE64"]
        )), 32)
        self.assertNotEqual(payload["proposed_secrets"]["PAYMENT_SERVER_API_KEY"], "test-only")

    def test_rejects_insecure_or_credential_bearing_urls(self):
        for url in ("http://ai.example/", "https://user:pass@ai.example/",
                    "https://ai.example/?token=x", "https://ai.example/#x"):
            with self.subTest(url=url):
                values = {**fixture(), "AI_CHAT_BASE_URL": url}
                with self.assertRaises(ValueError):
                    recovery.prepare_payload(values, "test-only")

    def test_rejects_invalid_sse_bounds(self):
        for total in ("180s", "30s", "0s", "-1s", "2147483648ms"):
            with self.subTest(total=total):
                with self.assertRaises(ValueError):
                    recovery.prepare_payload(
                        {**fixture(), "AI_CHAT_SSE_TOTAL_TIMEOUT": total}, "test-only"
                    )

    def test_rejects_invalid_capacity(self):
        for maximum in ("0", "-1", "1.5", "2147483648", "TBD"):
            with self.subTest(maximum=maximum), self.assertRaises(ValueError):
                recovery.prepare_payload(
                    {**fixture(), "AI_CHAT_SSE_MAX_CONNECTIONS": maximum}, "test-only"
                )

    def test_rejects_missing_or_unsafe_token(self):
        for token in ("", "has space", "line\nbreak"):
            with self.subTest(token=repr(token)), self.assertRaises(ValueError):
                recovery.prepare_payload(fixture(), token)

    def test_rejects_missing_public_input(self):
        values = fixture()
        del values["AI_CHAT_READ_TIMEOUT"]
        with self.assertRaises(ValueError):
            recovery.prepare_payload(values, "test-only")


if __name__ == "__main__":
    unittest.main()
