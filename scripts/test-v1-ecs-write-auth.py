"""실제 비밀/HTTP 호출 없이 단기 시험 인증 계약 검사."""
import base64
import hashlib
import hmac
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

spec = importlib.util.spec_from_file_location("auth", Path(__file__).with_name("prepare-v1-ecs-write-auth.py"))
auth = importlib.util.module_from_spec(spec)
spec.loader.exec_module(auth)


class WriteAuthTest(unittest.TestCase):
    def test_trial_token_claims_and_signature(self):
        key = b"a" * 32
        token = auth.make_token(base64.b64encode(key).decode(), 524, 1000)
        header, payload, signature = token.split(".")
        decode = lambda value: base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        self.assertEqual(json.loads(decode(payload)), {"iss": "seonjalal-staging", "sub": "524", "iat": 1000, "exp": 2800})
        self.assertEqual(decode(signature), hmac.new(key, (header + "." + payload).encode(), hashlib.sha256).digest())

    def test_wrong_subject_is_rejected(self):
        with self.assertRaises(auth.runtime.SafetyStop):
            auth.make_token(base64.b64encode(b"a" * 32).decode(), 1, 1000)

    def preflight(self, price):
        response = MagicMock()
        response.status = 200
        response.headers = {"X-Rehearsal-Upstream": "v1"}
        response.read.return_value = json.dumps({"data": {"recipient": {"userId": 577},
            "product": {"productId": 4, "unitPrice": price, "quantity": 1, "maxOrderQuantity": 10}}}).encode()
        with patch.object(auth.urllib.request, "build_opener") as build:
            build.return_value.open.return_value.__enter__.return_value = response
            plan = {"product_id": 4, "recipient_id": 577, "expected_unit_price": 22900}
            if price == 22900:
                auth.auth_preflight(plan, "fake-test-token")
                request = build.return_value.open.call_args.args[0]
                self.assertEqual(request.full_url, "http://127.0.0.1:18084/gifts/preflight")
                self.assertEqual(request.method, "POST")
                self.assertNotIn("/gifts?", request.full_url)
            else:
                with self.assertRaises(auth.runtime.SafetyStop):
                    auth.auth_preflight(plan, "fake-test-token")

    def test_preflight_calls_only_condition_query(self):
        self.preflight(22900)

    def test_changed_price_is_rejected(self):
        self.preflight(1000)

    def test_connection_refused_is_reported_without_url_or_secret(self):
        message = auth.connection_failure(auth.urllib.error.URLError(ConnectionRefusedError(61, "private detail")))
        self.assertIn("18084 연결 거부", message)
        self.assertNotIn("private detail", message)


if __name__ == "__main__":
    unittest.main()
