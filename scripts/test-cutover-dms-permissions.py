"""실제 RDS 변경 없이 신규 schema/권한 범위·부분 실패·승인 검증."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location(
    "dms_permissions", Path(__file__).with_name("prepare-cutover-dms-permissions.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class PermissionTest(unittest.TestCase):
    def original(self):
        return {"*.*": {"USAGE"}, "`awsdms_control`.*": {"ALL PRIVILEGES"},
                "`seonjalal_develop`.*": module.PRIVILEGES}

    def metadata(self):
        return {"local_infile": 1, "control_schema_exists": False,
                "accounts": [{"host": "%", "ssl_requirement": "ANY", "direct_grants": [
                    {"scope": key, "privileges": list(value), "grant_option": False}
                    for key, value in self.original().items()]}]}

    def test_db_underscore_is_escaped_when_wildcards_apply(self):
        self.assertEqual(module.scope(module.access.TARGET_SCHEMA, 0),
                         "`seonjalal\\_cutover\\_20261019`.*")
        self.assertEqual(module.scope(module.access.TARGET_SCHEMA, 1),
                         "`seonjalal_cutover_20261019`.*")
        with self.assertRaises(module.runtime.SafetyStop):
            module.scope("other", 0)

    def test_preflight_is_read_only(self):
        cursor = Mock()
        cursor.fetchone.return_value = (0,)
        with patch.object(module.access, "inspect_target", return_value=self.metadata()):
            original, partial = module.preflight(cursor)
        self.assertEqual(original, self.original())
        self.assertEqual(partial, 0)
        cursor.execute.assert_called_once_with("SELECT @@GLOBAL.partial_revokes")

    def test_expanded_existing_rights_stop(self):
        metadata = self.metadata()
        metadata["accounts"][0]["direct_grants"][0]["privileges"] = ["ALL PRIVILEGES"]
        cursor = Mock()
        with patch.object(module.access, "inspect_target", return_value=metadata):
            with self.assertRaises(module.runtime.SafetyStop):
                module.preflight(cursor)
        cursor.execute.assert_not_called()

    def test_apply_adds_only_two_scopes_and_control_schema(self):
        cursor = Mock()
        new = dict(self.original())
        new[module.scope(module.access.TARGET_SCHEMA, 0)] = module.PRIVILEGES
        new[module.scope(module.access.CONTROL_SCHEMA, 0)] = {"ALL PRIVILEGES"}
        grants = [("GRANT " + ", ".join(sorted(value)) + " ON " + key + " TO 'test'@'%'",)
                  for key, value in new.items()]
        cursor.fetchall.side_effect = [grants, (("%", "ANY"),)]
        cursor.fetchone.return_value = (0,)
        with tempfile.TemporaryDirectory() as directory:
            record = Path(directory) / "record.json"
            with patch.object(module, "preflight", return_value=(self.original(), 0)):
                module.apply_permissions(cursor, record)
            self.assertIn("scoped_grants_verified", record.read_text())
        writes = [call for call in cursor.execute.call_args_list
                  if not call.args[0].startswith(("SELECT ", "SHOW "))]
        self.assertEqual(len(writes), 3)
        self.assertEqual(writes[0].args, (module.CONTROL_CREATE,))
        self.assertIn(module.scope(module.access.TARGET_SCHEMA, 0), writes[1].args[0])
        self.assertIn(module.scope(module.access.CONTROL_SCHEMA, 0), writes[2].args[0])
        self.assertEqual(writes[1].args[1], ("dms_rehearsal", "%"))
        self.assertFalse(any("ON *.*" in call.args[0] for call in writes))

    def test_partial_failure_preserved_without_revoke_or_drop(self):
        cursor = Mock()
        def execute(sql, *args):
            if sql.startswith("GRANT "):
                raise RuntimeError("synthetic grant failure")
        cursor.execute.side_effect = execute
        with tempfile.TemporaryDirectory() as directory:
            record = Path(directory) / "record.json"
            with patch.object(module, "preflight", return_value=(self.original(), 0)):
                with self.assertRaises(RuntimeError):
                    module.apply_permissions(cursor, record)
            self.assertIn("partial_or_verification_failure", record.read_text())
        self.assertFalse(any(call.args[0].startswith(("DROP ", "REVOKE ", "ALTER USER"))
                             for call in cursor.execute.call_args_list))

    def test_existing_record_stops_before_db_read_or_write(self):
        with tempfile.TemporaryDirectory() as directory:
            record = Path(directory) / "record.json"
            record.touch()
            cursor = Mock()
            with self.assertRaises(module.runtime.SafetyStop):
                module.apply_permissions(cursor, record)
            cursor.execute.assert_not_called()

    def test_apply_without_approval_stops_before_credentials(self):
        with patch("sys.argv", ["grants", "--directory", "/unused", "--apply"]):
            with patch.object(module.access.helper, "management_credentials") as credentials:
                with self.assertRaises(module.runtime.SafetyStop):
                    module.main()
                credentials.assert_not_called()


if __name__ == "__main__":
    unittest.main()
