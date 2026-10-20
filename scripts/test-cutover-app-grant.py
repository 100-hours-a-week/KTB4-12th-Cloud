"""실제 DB 없이 scoped GRANT 단일 변경·실패 보존·사후검증 검사."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location("app_grant", Path(__file__).with_name("prepare-cutover-app-access.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class AppGrantTest(unittest.TestCase):
    def plan(self):
        return {"existing_grants": [{"scope": "*.*", "privileges": ["USAGE"], "grant_option": False},
                 {"scope": "`seonjalal_develop`.*", "privileges": ["SELECT", "INSERT", "UPDATE", "DELETE"],
                  "grant_option": False}], "new_scope": "`seonjalal\\_cutover\\_20261019`.*"}

    def cursor(self):
        cursor = Mock()
        cursor.fetchall.return_value = [
            ("GRANT USAGE ON *.* TO 'app'@'host'",),
            ("GRANT SELECT, INSERT, UPDATE, DELETE ON `seonjalal_develop`.* TO 'app'@'host'",),
            ("GRANT SELECT, INSERT, UPDATE, DELETE ON `seonjalal\\_cutover\\_20261019`.* TO 'app'@'host'",)]
        cursor.fetchone.side_effect = [("ANY",), (0,)]
        return cursor

    def test_single_scoped_grant_and_preserved_old_rights(self):
        with tempfile.TemporaryDirectory() as directory:
            cursor = self.cursor()
            with patch.object(module.access, "inspect", return_value=self.plan()):
                module.add_permissions(cursor, Path(directory))
            changes = [call for call in cursor.execute.call_args_list if call.args[0].startswith("GRANT ")]
            self.assertEqual(len(changes), 1)
            self.assertEqual(changes[0].args, (
                "GRANT SELECT, INSERT, UPDATE, DELETE ON `seonjalal\\_cutover\\_20261019`.* TO %s@%s",
                (module.access.USER, module.access.HOST)))
            record = json.loads((Path(directory) / module.RECORD).read_text())
            self.assertEqual(record["phase"], "scoped_grant_verified")

    def test_existing_record_blocks_before_preflight_or_sql(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / module.RECORD).touch()
            cursor = self.cursor()
            with patch.object(module.access, "inspect") as read:
                with self.assertRaises(module.runtime.SafetyStop):
                    module.add_permissions(cursor, Path(directory))
                read.assert_not_called()
            cursor.execute.assert_not_called()

    def test_lost_grant_response_keeps_intent_no_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            cursor = self.cursor()
            cursor.execute.side_effect = RuntimeError("synthetic lost reply")
            with patch.object(module.access, "inspect", return_value=self.plan()):
                with self.assertRaises(RuntimeError):
                    module.add_permissions(cursor, Path(directory))
            self.assertEqual(cursor.execute.call_count, 1)
            record = json.loads((Path(directory) / module.RECORD).read_text())
            self.assertEqual(record["phase"], "grant_result_or_verification_uncertain")

    def test_missing_old_rights_fails_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            cursor = self.cursor()
            cursor.fetchall.return_value = cursor.fetchall.return_value[:1]
            with patch.object(module.access, "inspect", return_value=self.plan()):
                with self.assertRaises(module.runtime.SafetyStop):
                    module.add_permissions(cursor, Path(directory))

    def test_unapproved_apply_stops_before_credentials(self):
        with patch("sys.argv", ["grant", "--directory", "/unused", "--apply"]):
            with patch.object(module.runtime, "protected_directory") as directory:
                with self.assertRaises(module.runtime.SafetyStop):
                    module.main()
                directory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
