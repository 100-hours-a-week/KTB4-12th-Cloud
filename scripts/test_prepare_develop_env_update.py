"""실제 서버 없이 공개 설정 갱신과 비밀 비전송 검증."""
import importlib.util
import json
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("env_update", Path(__file__).with_name("prepare-develop-env-update.py"))
update = importlib.util.module_from_spec(spec)
spec.loader.exec_module(update)


class UpdateTests(unittest.TestCase):
    def test_preserve_other_lines(self):
        original = "# 보존\nDB_PASSWORD=canary\nDB_NAME=existing\n" + "".join(k + "=" + v + "\n" for k, v in update.OLD_IMAGES.items())
        result = update.patch_env(original, {**update.IMAGES, "PRODUCT_SEARCH_ENABLED": "false"}, update.OLD_IMAGES)
        self.assertTrue(result.startswith("# 보존\nDB_PASSWORD=canary\nDB_NAME=existing\n"))
        self.assertIn("PRODUCT_SEARCH_ENABLED=false", result)
        self.assertEqual(update.patch_env(result, {**update.IMAGES, "PRODUCT_SEARCH_ENABLED": "false"}, update.OLD_IMAGES), result)

    def test_stale_and_duplicate_stop(self):
        for text in ("", "FRONTEND_IMAGE=unexpected\n", "FRONTEND_IMAGE=a\nFRONTEND_IMAGE=b\n"):
            with self.assertRaises(ValueError):
                update.patch_env(text, update.IMAGES, update.OLD_IMAGES)

    def test_no_secrets_in_request(self):
        values = {"AI_CHAT_BASE_URL": "https://example.invalid", "AI_CHAT_CONNECT_TIMEOUT": "1s",
                  "AI_CHAT_READ_TIMEOUT": "5s", "AI_CHAT_SSE_IDLE_TIMEOUT": "1m",
                  "AI_CHAT_SSE_TOTAL_TIMEOUT": "5m", "AI_CHAT_SSE_MAX_CONNECTIONS": "10"}
        payload = update.merge.inputs.prepare_payload(values, "SECRET_CANARY_DO_NOT_TRANSFER")
        result = json.dumps(update.build_request(payload))
        for value in payload["proposed_secrets"].values():
            self.assertNotIn(value, result)
        self.assertNotIn("proposed_secrets", result)
        self.assertNotIn("docker compose", result)
        payload["public"]["DB_PASSWORD"] = "forbidden"
        with self.assertRaises(ValueError):
            update.build_request(payload)


if __name__ == "__main__":
    unittest.main()
