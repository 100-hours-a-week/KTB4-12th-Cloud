"""실제 DB/AWS 호출 없이 쓰기 전 기준 조회의 읽기 범위·차단 조건 검사."""
from decimal import Decimal
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

spec = importlib.util.spec_from_file_location("fixture", Path(__file__).with_name("prepare-v1-ecs-write-fixture.py"))
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


class FixtureTest(unittest.TestCase):
    def exercise(self, *, missing_index=False, no_friend=False):
        cursor = MagicMock()
        columns = {"sender_id", "recipient_id", "product_id", "quantity", "product_price_snapshot",
                   "status", "idempotency_key", "request_fingerprint"}
        cursor.fetchall.side_effect = [
            [(str(n), 1) for n in range(1, 13)], [("gift_histories",), ("users",), ("products",)],
            [(524, "loadtest001@seonjalal.invalid", "ACTIVE", None)],
            [] if no_friend else [(577, "loadtest054@seonjalal.invalid", "ACTIVE", None)],
            [(key,) for key in columns], [] if missing_index else [("sender_id", 0), ("idempotency_key", 0)],
        ]
        cursor.fetchone.side_effect = [(Decimal("1000"), 87, None), (5022, 5022)]
        with tempfile.TemporaryDirectory(prefix="write-fixture-unit-") as directory:
            with patch("sys.argv", ["test", "--directory", directory]), \
                 patch.object(fixture.runtime, "protected_directory", return_value=Path(directory)), \
                 patch.object(fixture.candidate_db, "management_credentials", return_value={"username": "test", "password": "fake"}), \
                 patch.object(fixture.runtime, "connect_db") as connect, \
                 patch.object(fixture.runtime, "secure_write") as write:
                connect.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value = cursor
                if missing_index or no_friend:
                    with self.assertRaises(fixture.runtime.SafetyStop):
                        fixture.main()
                    write.assert_not_called()
                else:
                    fixture.main()
                    record = write.call_args.args[1]
                    self.assertEqual(len(set(record["request_keys"])), 30)
                    self.assertEqual(record["recipient_id"], 577)
                    self.assertFalse(record["execution_approved"])
                    self.assertFalse(record["business_write_executed"])
                for call in cursor.execute.call_args_list:
                    self.assertIn(call.args[0].split()[0], ("SELECT", "START", "ROLLBACK"))

    def test_preflight_only_reads_and_proposes_bounded_keys(self):
        self.exercise()

    def test_missing_idempotency_constraint_stops_plan(self):
        self.exercise(missing_index=True)

    def test_nonfriend_users_stop_plan(self):
        self.exercise(no_friend=True)


if __name__ == "__main__":
    unittest.main()
