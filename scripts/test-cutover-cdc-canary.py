"""실제 API/DB/AWS 없이 단일 요청·Secret 비노출·실패 경계 검사."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("canary", Path(__file__).with_name("run-cutover-cdc-canary.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class CanaryTest(unittest.TestCase):
    def baseline(self):
        return {"target_schema": module.loaded.access.TARGET_SCHEMA, "gift_rows": 5096,
                "latest_gift_id": 5096, "distinct_nonnull_gift_keys": 5096,
                "target_stock": [{"id": 4, "quantity": 13}]}

    def test_stale_baseline_stops(self):
        value = self.baseline()
        module.check_baseline(value)
        value["gift_rows"] = 5097
        with self.assertRaises(module.runtime.SafetyStop):
            module.check_baseline(value)

    def test_exactly_one_login_and_one_gift_no_secret_in_record(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(module, "post", side_effect=[{}, {"data": {"gift": {"giftId": 5097}}}]) as post:
                with patch.object(module.login, "login_data", return_value=("private-token", 9999999999)):
                    with patch.object(module, "read_baseline"):
                        module.run_canary(Path(directory), 13306, "private-password", self.baseline())
                self.assertEqual(post.call_count, 2)
                self.assertEqual(post.call_args_list[0].args[0], "/api/auth/login")
                self.assertEqual(post.call_args_list[1].args[0], "/api/gifts")
            raw = (Path(directory) / module.RECORD).read_text()
            self.assertNotIn("private-token", raw)
            self.assertNotIn("private-password", raw)
            self.assertEqual(json.loads(raw)["phase"], "gift_http_succeeded")

    def test_lost_gift_reply_preserves_key_and_never_retries(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(module, "post", side_effect=[{}, RuntimeError("lost")]) as post:
                with patch.object(module.login, "login_data", return_value=("token", 9999999999)):
                    with patch.object(module, "read_baseline"):
                        with self.assertRaises(RuntimeError):
                            module.run_canary(Path(directory), 13306, "password", self.baseline())
                self.assertEqual(post.call_count, 2)
            record = json.loads((Path(directory) / module.RECORD).read_text())
            self.assertEqual(record["phase"], "gift_requested")
            self.assertIn("request_id", record)

    def test_duplicate_intent_blocks_http(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / module.RECORD).touch()
            with patch.object(module, "post") as post:
                with self.assertRaises(FileExistsError):
                    module.run_canary(Path(directory), 13306, "password", self.baseline())
                post.assert_not_called()

    def test_baseline_changed_after_login_blocks_gift(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(module, "post", return_value={}) as post:
                with patch.object(module.login, "login_data", return_value=("token", 9999999999)):
                    with patch.object(module, "read_baseline", side_effect=module.runtime.SafetyStop("changed")):
                        with self.assertRaises(module.runtime.SafetyStop):
                            module.run_canary(Path(directory), 13306, "password", self.baseline())
                self.assertEqual(post.call_count, 1)

    def test_curl_secrets_only_in_stdin_no_redirect_or_retry(self):
        result = subprocess.CompletedProcess([], 0, '{"data":{}}\n201', '')
        with patch.object(module.subprocess, "run", return_value=result) as run:
            module.post("/api/gifts", {"private": "private-password"}, 201,
                        "private-token", "938d03b7-30c4-48a7-bbd4-951829067399")
            args = run.call_args.args[0]
            self.assertNotIn("private-token", " ".join(args))
            self.assertNotIn("private-password", " ".join(args))
            self.assertNotIn("--location", args)
            self.assertEqual(args[args.index("--retry") + 1], "0")
            self.assertIn("private-token", run.call_args.kwargs["input"])

    def test_missing_approval_prevents_credentials_or_http(self):
        with patch("sys.argv", ["canary", "--directory", "/unused"]):
            with patch.object(module, "read_baseline") as read:
                with self.assertRaises(module.runtime.SafetyStop):
                    module.main()
                read.assert_not_called()


if __name__ == "__main__":
    unittest.main()
