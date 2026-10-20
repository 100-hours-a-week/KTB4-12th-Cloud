"""실제 DB 접속 없이 CDC 동일 키·원장·재고 읽기 검증 경계 확인."""
from decimal import Decimal
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
import uuid

spec = importlib.util.spec_from_file_location("verification", Path(__file__).with_name("verify-cutover-cdc-canary.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class VerificationTest(unittest.TestCase):
    def record(self):
        return {"task_arn": module.canary.TASK_ARN, "target_schema": module.SCHEMA,
                "phase": "gift_http_succeeded", "sender_id": 524, "recipient_id": 577,
                "product_id": 4, "quantity": 1, "unit_price": 22900, "before_gifts": 5096,
                "before_stock": 13, "gift_id": 5097, "request_id": "938d03b7-30c4-48a7-bbd4-951829067399"}

    def cursor(self, stock=12, keys=5097, missing=False):
        cursor = Mock()
        key = uuid.UUID(self.record()["request_id"]).hex.upper()
        cursor.fetchall.return_value = [] if missing else [(5097, 524, 577, 4, 1, Decimal(22900), "COMPLETED", key, 1)]
        cursor.fetchone.side_effect = [(1,), (Decimal(22900), stock), (5097, keys, 5097), (5053,)]
        return cursor

    def test_success_only_reads(self):
        cursor = self.cursor()
        result = module.inspect(cursor, self.record())
        self.assertTrue(result["same_idempotency_key_verified"])
        self.assertEqual(result["product_4_stock"], 12)
        for call in cursor.execute.call_args_list:
            self.assertTrue(call.args[0].startswith(("SELECT", "START TRANSACTION READ ONLY", "ROLLBACK")))

    def test_missing_row_stops_and_rolls_back(self):
        cursor = self.cursor(missing=True)
        with self.assertRaises(module.runtime.SafetyStop):
            module.inspect(cursor, self.record())
        self.assertEqual(cursor.execute.call_args.args[0], "ROLLBACK")

    def test_wrong_stock_stops(self):
        with self.assertRaises(module.runtime.SafetyStop):
            module.inspect(self.cursor(stock=13), self.record())

    def test_wrong_distinct_key_count_stops(self):
        with self.assertRaises(module.runtime.SafetyStop):
            module.inspect(self.cursor(keys=5096), self.record())

    def test_record_requires_success_and_private_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / module.canary.RECORD
            record = self.record()
            path.write_text(json.dumps(record))
            path.chmod(0o600)
            self.assertEqual(module.read_record(Path(directory))["gift_id"], 5097)
            record["phase"] = "gift_requested"
            path.write_text(json.dumps(record))
            with self.assertRaises(module.runtime.SafetyStop):
                module.read_record(Path(directory))


if __name__ == "__main__":
    unittest.main()
