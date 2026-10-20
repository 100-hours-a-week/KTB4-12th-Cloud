"""실제 AWS/DB 접속 없이 Target 읽기 전용 검사 확인."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock

spec = importlib.util.spec_from_file_location(
    "loaded", Path(__file__).with_name("inspect-cutover-loaded-target.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class LoadedTargetTest(unittest.TestCase):
    def cursor(self, fk_count=18):
        cursor = Mock()
        cursor.fetchall.side_effect = [
            [(name,) for name in module.access.TABLES], [(3, 59), (4, 13)]]
        cursor.fetchone.side_effect = [(1,)] * 16 + [(fk_count,), (1, 1, 5096), (5053,)]
        return cursor

    def test_readonly_location_and_counts(self):
        cursor = self.cursor()
        result = module.inspect(cursor)
        self.assertEqual(result["target_total_rows"], 16)
        self.assertEqual(result["existing_rds_schema_gift_count"], 5053)
        commands = [call.args[0] for call in cursor.execute.call_args_list]
        self.assertEqual(commands[0], "START TRANSACTION READ ONLY")
        self.assertEqual(commands[-1], "ROLLBACK")
        self.assertTrue(all(sql.startswith(("SELECT", "START TRANSACTION READ ONLY", "ROLLBACK"))
                            for sql in commands))

    def test_wrong_tables_stop_and_rollback(self):
        cursor = self.cursor()
        cursor.fetchall.side_effect = [[("unexpected",)]]
        with self.assertRaises(module.runtime.SafetyStop):
            module.inspect(cursor)
        self.assertEqual(cursor.execute.call_args.args[0], "ROLLBACK")

    def test_wrong_fk_count_stop_and_rollback(self):
        cursor = self.cursor(fk_count=17)
        with self.assertRaises(module.runtime.SafetyStop):
            module.inspect(cursor)
        self.assertEqual(cursor.execute.call_args.args[0], "ROLLBACK")


if __name__ == "__main__":
    unittest.main()
