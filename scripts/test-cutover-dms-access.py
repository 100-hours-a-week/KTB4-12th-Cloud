"""실제 DMS/DB 호출 없이 출력 비밀 제거와 읽기 전용 범위 검사."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock

spec = importlib.util.spec_from_file_location(
    "dms_access", Path(__file__).with_name("inspect-cutover-dms-access.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class DmsAccessTest(unittest.TestCase):
    def test_endpoint_does_not_echo_connection_script_or_password(self):
        row = {"EndpointIdentifier": "test", "EndpointType": "target", "SslMode": "verify-full",
               "ExtraConnectionAttributes": "private arbitrary script",
               "MySQLSettings": {"Password": "private password", "AfterConnectScript": "private SQL"}}
        output = str(module.endpoint_summary(row))
        self.assertNotIn("private", output)
        self.assertIn("'other_extra_attributes_present': True", output)

    def test_only_expected_fk_initstmt_is_recognized(self):
        row = {"EndpointIdentifier": "test", "EndpointType": "target",
               "ExtraConnectionAttributes": "Initstmt=SET FOREIGN_KEY_CHECKS=0;"}
        self.assertTrue(module.endpoint_summary(row)["fk_disable_initstmt"])
        row["ExtraConnectionAttributes"] += "unexpected=1;"
        self.assertFalse(module.endpoint_summary(row)["fk_disable_initstmt"])

    def test_grant_summary_hides_principal_and_auth_suffix(self):
        result = module.summarize_grants([(
            "GRANT SELECT, INSERT ON `schema`.* TO 'private-principal'@'%' WITH GRANT OPTION",)])
        self.assertEqual(result, [{"scope": "`schema`.*", "privileges": ["SELECT", "INSERT"],
                                   "grant_option": True}])

    def test_metadata_sql_is_read_only(self):
        cursor = Mock()
        cursor.fetchone.side_effect = [(1, 1), *((0,) for _ in module.TABLES), (0,)]
        cursor.fetchall.side_effect = [[(name,) for name in module.TABLES], [("%", "ANY")],
                                       [("GRANT SELECT ON `old`.* TO 'test'@'%'",)]]
        result = module.inspect_target(cursor, "test")
        self.assertEqual(result["target_total_rows"], 0)
        self.assertEqual(result["local_infile"], 1)
        for call in cursor.execute.call_args_list:
            self.assertTrue(call.args[0].startswith(("SELECT ", "SHOW GRANTS ",
                                                    "START TRANSACTION READ ONLY", "ROLLBACK")))
        self.assertEqual(cursor.execute.call_args_list[-1].args, ("ROLLBACK",))

    def test_changed_inventory_stops_and_rolls_back(self):
        cursor = Mock()
        cursor.fetchone.return_value = (1, 1)
        cursor.fetchall.return_value = [("unexpected",)]
        with self.assertRaises(module.runtime.SafetyStop):
            module.inspect_target(cursor, "test")
        self.assertEqual(cursor.execute.call_args_list[-1].args, ("ROLLBACK",))

    def test_nonempty_target_stops_without_grants_change(self):
        cursor = Mock()
        cursor.fetchone.side_effect = [(1, 1), (1,)]
        cursor.fetchall.return_value = [(name,) for name in module.TABLES]
        with self.assertRaises(module.runtime.SafetyStop):
            module.inspect_target(cursor, "test")
        self.assertFalse(any(call.args[0].startswith("GRANT ") for call in cursor.execute.call_args_list))


if __name__ == "__main__":
    unittest.main()
