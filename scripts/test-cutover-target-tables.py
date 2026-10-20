"""실제 RDS 변경 없이 고정 DDL 추출·빈 Target·부분 생성 보호 검사."""
import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location(
    "target_tables", Path(__file__).with_name("prepare-cutover-target-tables.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class TargetTableTest(unittest.TestCase):
    def raw(self):
        return ("/*!50503 SET NAMES utf8mb4 */;\n" + "\n".join(
            f"CREATE TABLE `{name}` (`id` bigint PRIMARY KEY) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;"
            for name in module.source.TABLES)).encode()

    def statements(self):
        raw = self.raw()
        with patch.object(module, "SOURCE_HASH", hashlib.sha256(raw).hexdigest()):
            return module.parse_ddl(raw)

    def test_only_16_create_statements_extracted(self):
        statements = self.statements()
        self.assertEqual([name for name, _ in statements], module.source.TABLES)
        self.assertTrue(all(sql.startswith("CREATE TABLE `") for _, sql in statements))
        self.assertFalse(any("SET NAMES" in sql for _, sql in statements))

    def test_changed_hash_stops(self):
        with self.assertRaises(module.helper.runtime.SafetyStop):
            module.parse_ddl(self.raw())

    def test_extra_sql_rejected_even_with_matching_hash(self):
        raw = self.raw() + b"\nSELECT 1;"
        with patch.object(module, "SOURCE_HASH", hashlib.sha256(raw).hexdigest()):
            with self.assertRaises(module.helper.runtime.SafetyStop):
                module.parse_ddl(raw)

    def test_nonempty_target_stops_before_any_create(self):
        cursor = Mock()
        cursor.fetchone.side_effect = [("utf8mb4", "utf8mb4_0900_ai_ci"), (1,)]
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(module.helper.runtime.SafetyStop):
                module.apply_tables(Mock(), cursor, self.statements(), Path(directory) / "state.json")
        self.assertTrue(all(call.args[0].startswith("SELECT ") for call in cursor.execute.call_args_list))

    def test_partial_failure_restores_session_and_preserves_record(self):
        cursor = Mock()
        cursor.fetchone.side_effect = [("utf8mb4", "utf8mb4_0900_ai_ci"), (0,), (module.TARGET, 1)]
        def execute(sql, *args):
            if sql.startswith("CREATE TABLE"):
                raise RuntimeError("synthetic DDL failure")
        cursor.execute.side_effect = execute
        with tempfile.TemporaryDirectory() as directory:
            record = Path(directory) / "state.json"
            with self.assertRaises(RuntimeError):
                module.apply_tables(Mock(), cursor, self.statements(), record)
            self.assertTrue(record.exists())
            self.assertIn("partial_or_verification_failure", record.read_text())
        self.assertEqual(cursor.execute.call_args_list[-1].args, ("SET SESSION foreign_key_checks=1",))
        self.assertFalse(any("DROP" in call.args[0] for call in cursor.execute.call_args_list))

    def test_wrong_selected_schema_never_creates_tables(self):
        cursor = Mock()
        cursor.fetchone.side_effect = [("utf8mb4", "utf8mb4_0900_ai_ci"), (0,), ("other", 1)]
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(module.helper.runtime.SafetyStop):
                module.apply_tables(Mock(), cursor, self.statements(), Path(directory) / "state.json")
        self.assertFalse(any(call.args[0].startswith("CREATE TABLE") for call in cursor.execute.call_args_list))

    def test_apply_without_approval_stops_before_artifact_or_aws(self):
        with patch("sys.argv", ["tables", "--directory", "/unused", "--schema-file", "/unused", "--apply"]):
            with patch.object(module, "read_artifact") as artifact:
                with self.assertRaises(module.helper.runtime.SafetyStop):
                    module.main()
                artifact.assert_not_called()


if __name__ == "__main__":
    unittest.main()
