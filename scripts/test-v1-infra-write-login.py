import base64
import importlib.util
import json
from pathlib import Path
import unittest
from datetime import datetime, timezone

spec = importlib.util.spec_from_file_location("write_login", Path(__file__).with_name("prepare-v1-infra-write-login.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def payload(user_id=524, email=module.EMAIL, expiration=5000, subject="524"):
    claims = base64.urlsafe_b64encode(json.dumps({"sub": subject, "exp": expiration}).encode()).decode().rstrip("=")
    return {"data": {"user": {"userId": user_id, "email": email},
                     "accessToken": "synthetic." + claims + ".signature"}}


class LoginTest(unittest.TestCase):
    def v2_plan(self):
        return {"account": "721744297924", "db": "seonjalal-v2-migration-rehearsal",
                "schema": "seonjalal_develop", "sender_id": 524, "recipient_id": 577,
                "product_id": 4, "quantity": 1, "expected_unit_price": 22900,
                "before_stock": 87, "before_gift_count": 5022, "before_latest_gift_id": 5022,
                "max_new_gifts": 30, "prepared_utc": datetime.fromtimestamp(1000, timezone.utc).isoformat()}

    def test_v2_uses_its_own_preflight_baseline(self):
        self.assertEqual(module.v2_baseline(self.v2_plan(), 1100)["product4Stock"], 87)

    def test_v2_wrong_database(self):
        value = self.v2_plan()
        value["db"] = "production"
        with self.assertRaises(module.SafetyStop):
            module.v2_baseline(value, 1100)

    def test_v2_wrong_recipient(self):
        value = self.v2_plan()
        value["recipient_id"] = 524
        with self.assertRaises(module.SafetyStop):
            module.v2_baseline(value, 1100)

    def test_v2_stale_preflight(self):
        with self.assertRaises(module.SafetyStop):
            module.v2_baseline(self.v2_plan(), 2000)

    def test_v2_needs_stock_for_smoke_and_baseline(self):
        value = self.v2_plan()
        value["before_stock"] = 30
        with self.assertRaises(module.SafetyStop):
            module.v2_baseline(value, 1100)

    def test_actual_user_contract(self):
        self.assertEqual(module.login_data(payload(), 1000)[1], 5000)

    def test_wrong_user(self):
        with self.assertRaises(module.SafetyStop):
            module.login_data(payload(user_id=1), 1000)

    def test_wrong_email(self):
        with self.assertRaises(module.SafetyStop):
            module.login_data(payload(email="other@example.invalid"), 1000)

    def test_wrong_token_subject(self):
        with self.assertRaises(module.SafetyStop):
            module.login_data(payload(subject="1"), 1000)

    def test_short_lived_token(self):
        with self.assertRaises(module.SafetyStop):
            module.login_data(payload(expiration=1500), 1000)

    def test_error_envelope(self):
        value = payload()
        value["error"] = {"code": "SYNTHETIC"}
        with self.assertRaises(module.SafetyStop):
            module.login_data(value, 1000)


if __name__ == "__main__":
    unittest.main()
