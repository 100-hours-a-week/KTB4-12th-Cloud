"""실제 AWS 호출 없이 신규 Task 요청·오류·승인·기존 자원 보존 검사."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "dms_task", Path(__file__).with_name("prepare-cutover-dms-task.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class TaskTest(unittest.TestCase):
    def response(self):
        body = module.request()
        return {"ReplicationTask": {key: body[key] for key in (
            "ReplicationTaskIdentifier", "SourceEndpointArn", "TargetEndpointArn",
            "ReplicationInstanceArn", "MigrationType")} | {
                "ReplicationTaskArn": "arn:aws:dms:ap-northeast-2:721744297924:task:TEST",
                "Status": "creating"}}

    def endpoint(self):
        return {"EndpointIdentifier": "test", "EndpointType": "TARGET", "SslMode": "verify-full",
                "CertificateArn": "test-ca", "MySQLSettings": {"SecretsManagerSecretId": "test-secret"},
                "ExtraConnectionAttributes": "Initstmt=SET FOREIGN_KEY_CHECKS=0;"}

    def test_mapping_lists_sixteen_names_and_renames_only_schema(self):
        rules = module.mappings()["rules"]
        self.assertEqual(len(rules), 17)
        selected = [row["object-locator"]["table-name"] for row in rules[:-1]]
        self.assertEqual(selected, module.access.TABLES)
        self.assertTrue(all(row["object-locator"]["schema-name"] == "seonjalal_develop" for row in rules))
        self.assertEqual(rules[-1]["value"], module.access.TARGET_SCHEMA)
        self.assertEqual(rules[-1]["rule-target"], "schema")
        self.assertNotIn("%", selected)

    def test_no_truncate_drop_or_source_ddl_application(self):
        settings = module.settings()
        self.assertEqual(settings["FullLoadSettings"]["TargetTablePrepMode"], "DO_NOTHING")
        self.assertEqual(set(settings["ChangeProcessingDdlHandlingPolicy"].values()), {False})
        self.assertEqual(settings["ControlTablesSettings"]["ControlSchema"], module.access.CONTROL_SCHEMA)
        self.assertTrue(settings["ValidationSettings"]["EnableValidation"])
        for name in ("DataErrorPolicy", "DataTruncationErrorPolicy", "TableErrorPolicy",
                     "ApplyErrorDeletePolicy", "ApplyErrorInsertPolicy", "ApplyErrorUpdatePolicy"):
            self.assertEqual(settings["ErrorBehavior"][name], "STOP_TASK")

    def test_forced_database_and_unknown_script_are_rejected(self):
        for key, value in (("DatabaseName", "old"), ("ExtraConnectionAttributes", "unexpected=1;")):
            item = self.endpoint()
            item[key] = value
            with self.assertRaises(module.runtime.SafetyStop):
                module.check_endpoint(item, "TARGET")

    def test_target_requires_verified_tls_and_ca(self):
        self.assertEqual(module.check_endpoint(self.endpoint(), "TARGET")["ssl_mode"], "verify-full")
        for key in ("SslMode", "CertificateArn"):
            item = self.endpoint()
            item[key] = "none" if key == "SslMode" else None
            with self.assertRaises(module.runtime.SafetyStop):
                module.check_endpoint(item, "TARGET")

    def test_apply_calls_only_create_never_start_or_modify(self):
        body = module.request()
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(module.runtime, "aws", return_value=self.response()) as aws:
                module.create_task(Path(directory), body)
                self.assertEqual(aws.call_count, 1)
                self.assertEqual(aws.call_args.args, ("dms", "create-replication-task"))
                self.assertEqual(aws.call_args.kwargs["body"], body)
            state = json.loads((Path(directory) / "cutover-dms-create-record.json").read_text())
            self.assertEqual(state["phase"], "created_not_started")

    def test_lost_create_response_preserves_intent_without_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(module.runtime, "aws", side_effect=RuntimeError("synthetic lost response")) as aws:
                with self.assertRaises(RuntimeError):
                    module.create_task(Path(directory), module.request())
                self.assertEqual(aws.call_count, 1)
            state = json.loads((Path(directory) / "cutover-dms-create-record.json").read_text())
            self.assertEqual(state["phase"], "create_requested")
            with patch.object(module.runtime, "aws") as aws:
                with self.assertRaises(module.runtime.SafetyStop):
                    module.create_task(Path(directory), module.request())
                aws.assert_not_called()

    def test_request_hash_is_stable_and_detects_target_changes(self):
        body = module.request()
        self.assertEqual(module.digest(body), module.digest(module.request()))
        body["TargetEndpointArn"] = "other"
        self.assertNotEqual(module.digest(body), module.digest(module.request()))

    def test_apply_without_approval_stops_before_read_or_write(self):
        with patch("sys.argv", ["task", "--directory", "/unused", "--apply"]):
            with patch.object(module, "preflight") as preflight:
                with self.assertRaises(module.runtime.SafetyStop):
                    module.main()
                preflight.assert_not_called()


if __name__ == "__main__":
    unittest.main()
