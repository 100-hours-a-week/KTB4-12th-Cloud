#!/usr/bin/env python3
"""전환 후 선물 검증의 로컬 모의 검사. API/AWS 실행 없음."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import MagicMock
import uuid

spec = importlib.util.spec_from_file_location("postwrite", Path(__file__).with_name("run-cutover-postwrite.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class Tests(unittest.TestCase):
    def fixture(self):
        key = str(uuid.uuid4())
        state = {"request_id": key, "baseline": {"gift_count":5097, "last_gift_id":5097, "stock":12}, "gift_id":5098}
        row = (5098,524,577,4,1,22900,"COMPLETED",uuid.UUID(key).hex.upper(),1)
        cursor = MagicMock()
        cursor.fetchall.return_value = [row]
        cursor.fetchone.side_effect = [(5098,5098),(11,)]
        return state, row, cursor

    def test_valid_gift_stock(self):
        state, _, cursor = self.fixture()
        result = module.verify(cursor, state)
        self.assertEqual(result["gift_id"], 5098)
        self.assertEqual(result["product_4_stock"], 11)
        cursor.execute.assert_any_call("ROLLBACK")

    def test_duplicate_key_rejected(self):
        state, row, cursor = self.fixture()
        cursor.fetchall.return_value = [row,row]
        with self.assertRaises(module.runtime.SafetyStop):
            module.verify(cursor, state)
        cursor.execute.assert_any_call("ROLLBACK")

    def test_wrong_stock_rejected(self):
        state, _, cursor = self.fixture()
        cursor.fetchone.side_effect = [(5098,5098),(12,)]
        with self.assertRaises(module.runtime.SafetyStop):
            module.verify(cursor, state)

    def test_http_id_mismatch_rejected(self):
        state, _, cursor = self.fixture()
        state["gift_id"] = 5100
        with self.assertRaises(module.runtime.SafetyStop):
            module.verify(cursor, state)

    def test_ambiguous_http_can_verify_by_key_without_post(self):
        state, _, cursor = self.fixture()
        state.pop("gift_id")
        self.assertEqual(module.verify(cursor, state)["gift_id"], 5098)


if __name__ == "__main__":
    unittest.main()
