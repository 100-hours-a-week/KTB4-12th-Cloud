"""실제 AWS/DB 접속 없이 읽기 전용 SQL과 실패 경계 검증."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location(
    "isolated", Path(__file__).with_name("inspect-isolated-cutover-db.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class IsolatedReadTest(unittest.TestCase):
    def cursor(self):
        cursor = Mock()
        cursor.fetchall.side_effect = [
            [(module.BASE_SCHEMA, "utf8mb4", "utf8mb4_0900_ai_ci")],
            [("BASE TABLE", 16, 1024)],
        ]
        cursor.fetchone.side_effect = [(0,), (0,)]
        return cursor

    def test_only_read_sql_and_fixed_schema(self):
        cursor = self.cursor()
        result = module.inspect(cursor)
        self.assertTrue(result["new_schema_absent"])
        self.assertEqual(result["tables"][0]["count"], 16)
        for call in cursor.execute.call_args_list:
            sql = call.args[0]
            self.assertTrue(sql.startswith(("SELECT ", "START TRANSACTION READ ONLY", "ROLLBACK")))
        self.assertEqual(cursor.execute.call_args_list[-1].args, ("ROLLBACK",))

    def test_existing_target_schema_stops_without_writes(self):
        cursor = self.cursor()
        cursor.fetchall.side_effect = [[
            (module.BASE_SCHEMA, "utf8mb4", "utf8mb4_0900_ai_ci"),
            (module.SCHEMA, "utf8mb4", "utf8mb4_0900_ai_ci"),
        ]]
        with self.assertRaises(module.helper.runtime.SafetyStop):
            module.inspect(cursor)
        self.assertEqual(cursor.execute.call_args_list[-1].args, ("ROLLBACK",))
        self.assertEqual(cursor.execute.call_count, 3)

    def test_external_fk_stops(self):
        cursor = self.cursor()
        cursor.fetchone.side_effect = [(0,), (1,)]
        with self.assertRaises(module.helper.runtime.SafetyStop):
            module.inspect(cursor)
        self.assertEqual(cursor.execute.call_args_list[-1].args, ("ROLLBACK",))

    def test_query_failure_still_rolls_back(self):
        cursor = Mock()
        cursor.fetchall.side_effect = RuntimeError("synthetic query failure")
        with self.assertRaises(RuntimeError):
            module.inspect(cursor)
        self.assertEqual(cursor.execute.call_args_list[-1].args, ("ROLLBACK",))

    def test_invalid_port_stops_before_credentials(self):
        with patch("sys.argv", ["inspect", "--directory", "/unused", "--local-port", "0"]):
            with patch.object(module.helper, "management_credentials") as credentials:
                with self.assertRaises(module.helper.runtime.SafetyStop):
                    module.main()
                credentials.assert_not_called()


if __name__ == "__main__":
    unittest.main()
