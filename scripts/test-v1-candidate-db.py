"""실제 DB/AWS 연결 없이 후보 계정 SQL 범위 검사."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location(
    "candidate", Path(__file__).with_name("prepare-v1-candidate-db.py")
)
candidate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(candidate)


class CandidateAccountTest(unittest.TestCase):
    def cursor(self):
        cursor = Mock()
        cursor.fetchone.return_value = ("ANY",)
        cursor.fetchall.return_value = [(
            "GRANT SELECT, INSERT, UPDATE, DELETE ON `seonjalal_develop`.* TO 'test'@'test'",
        )]
        return cursor

    def test_only_exact_host_create_and_schema_dml(self):
        cursor = self.cursor()
        candidate.create_candidate_account(cursor, "test-only-password")
        calls = cursor.execute.call_args_list
        self.assertEqual(calls[0].args[0], "CREATE USER %s@%s IDENTIFIED BY %s REQUIRE SSL")
        self.assertEqual(calls[0].args[1][:2], ("seonjalal_staging_app", "172.31.6.212"))
        self.assertEqual(calls[1].args[0], "GRANT SELECT, INSERT, UPDATE, DELETE ON `seonjalal_develop`.* TO %s@%s")
        self.assertEqual(calls[1].args[1], ("seonjalal_staging_app", "172.31.6.212"))
        self.assertEqual(len(calls), 4)

    def test_existing_user_creation_error_never_alters_or_grants(self):
        cursor = self.cursor()
        cursor.execute.side_effect = RuntimeError("simulated existing account")
        with self.assertRaises(RuntimeError):
            candidate.create_candidate_account(cursor, "test-only-password")
        self.assertEqual(cursor.execute.call_count, 1)

    def test_excess_privileges_rejected(self):
        cursor = self.cursor()
        cursor.fetchall.return_value = [("GRANT ALL PRIVILEGES ON *.* TO 'test'@'test'",)]
        with self.assertRaises(candidate.runtime.SafetyStop):
            candidate.create_candidate_account(cursor, "test-only-password")

    def test_apply_without_approval_stops_before_directory_or_aws_access(self):
        with patch("sys.argv", ["test", "--directory", "/unused", "--apply"]):
            with patch.object(candidate.runtime, "protected_directory") as directory:
                with self.assertRaises(candidate.runtime.SafetyStop):
                    candidate.main()
                directory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
