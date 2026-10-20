"""AWS/DB 접속 없이 빈 DB 생성 범위 및 승인/부분 실패 경계 검증."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location(
    "create_isolation", Path(__file__).with_name("prepare-isolated-cutover-db.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class EmptyDatabaseTest(unittest.TestCase):
    def metadata(self):
        return {"new_schema_absent": True, "new_schema": module.inspector.SCHEMA,
                "charset": "utf8mb4", "collation": "utf8mb4_0900_ai_ci"}

    def test_exact_create_and_only_metadata_verification(self):
        cursor = Mock()
        cursor.fetchone.side_effect = [("utf8mb4", "utf8mb4_0900_ai_ci"), (0,)]
        module.create_empty(cursor, self.metadata())
        self.assertEqual(cursor.execute.call_args_list[0].args, (module.CREATE_SQL,))
        self.assertNotIn("IF NOT EXISTS", module.CREATE_SQL)
        self.assertEqual(cursor.execute.call_count, 3)
        self.assertTrue(all(call.args[0].startswith("SELECT ")
                            for call in cursor.execute.call_args_list[1:]))

    def test_wrong_schema_stops_before_create(self):
        cursor = Mock()
        metadata = self.metadata()
        metadata["new_schema"] = "other"
        with self.assertRaises(module.helper.runtime.SafetyStop):
            module.create_empty(cursor, metadata)
        cursor.execute.assert_not_called()

    def test_existing_schema_stops_before_create(self):
        cursor = Mock()
        metadata = self.metadata()
        metadata["new_schema_absent"] = False
        with self.assertRaises(module.helper.runtime.SafetyStop):
            module.create_empty(cursor, metadata)
        cursor.execute.assert_not_called()

    def test_partial_failure_never_drops_or_retries(self):
        cursor = Mock()
        cursor.fetchone.return_value = ("wrong", "wrong")
        with self.assertRaises(module.helper.runtime.SafetyStop):
            module.create_empty(cursor, self.metadata())
        self.assertEqual(cursor.execute.call_count, 2)
        self.assertFalse(any("DROP" in call.args[0] for call in cursor.execute.call_args_list))

    def test_apply_without_approval_stops_before_credentials(self):
        with patch("sys.argv", ["create", "--directory", "/unused", "--apply"]):
            with patch.object(module.helper, "management_credentials") as credentials:
                with self.assertRaises(module.helper.runtime.SafetyStop):
                    module.main()
                credentials.assert_not_called()


if __name__ == "__main__":
    unittest.main()
