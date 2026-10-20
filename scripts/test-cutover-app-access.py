import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock

spec = importlib.util.spec_from_file_location("app_access", Path(__file__).with_name("inspect-cutover-app-access.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class AppAccessTest(unittest.TestCase):
    def cursor(self, tls="ANY", privilege="SELECT, INSERT, UPDATE, DELETE", partial=0):
        cursor = Mock()
        cursor.fetchone.side_effect = [(tls,), (partial,), (16,)]
        cursor.fetchall.return_value = [
            ("GRANT USAGE ON *.* TO `seonjalal_staging_app`@`172.31.6.212`",),
            ("GRANT " + privilege + " ON `seonjalal_develop`.* TO `seonjalal_staging_app`@`172.31.6.212`",)]
        return cursor

    def test_readonly_plan_escapes_database_wildcards(self):
        cursor = self.cursor()
        result = module.inspect(cursor)
        self.assertEqual(result["new_scope"], "`seonjalal\\_cutover\\_20261019`.*")
        self.assertTrue(all(call.args[0].startswith(("SELECT", "SHOW GRANTS"))
                            for call in cursor.execute.call_args_list))

    def test_unexpected_privileges_stop(self):
        with self.assertRaises(module.runtime.SafetyStop):
            module.inspect(self.cursor(privilege="ALL PRIVILEGES"))

    def test_missing_tls_stops(self):
        with self.assertRaises(module.runtime.SafetyStop):
            module.inspect(self.cursor(tls=""))

    def test_partial_revokes_uses_literal_database(self):
        result = module.inspect(self.cursor(partial=1))
        self.assertEqual(result["new_scope"], "`seonjalal_cutover_20261019`.*")


if __name__ == "__main__":
    unittest.main()
