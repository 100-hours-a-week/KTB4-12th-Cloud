"""실제 복제 없이 최초 시작 단일 호출·최신 점검·중복/응답유실 경계 검사."""
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location(
    "dms_start", Path(__file__).with_name("start-cutover-dms.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class DmsStartTest(unittest.TestCase):
    def report(self):
        return {"checked_at_utc": datetime.now(timezone.utc).isoformat(),
                "task_arn": module.inspection.TASK_ARN, "task_status": "ready", "settings_verified": True,
                "replication_started": False, "source_catalog_tables": 16, "target_tables": 16,
                "target_rows": 0, "source_ssl": "none", "target_ssl": "verify-full",
                "source_catalog": {"catalog": [["seonjalal_develop", name, "BASE TABLE", "InnoDB"]
                                               for name in module.inspection.access.TABLES],
                                   "binlog": ["1", "ROW", "FULL", "0", "0"],
                                   "scheduler_flags": ["false", "false"],
                                   "source_trigger_count": 0, "source_event_count": 0},
                "secret_versions": {"source": {"host_verified": True, "version_id": "one"},
                                    "target": {"host_verified": True, "version_id": "two"}}}

    def row(self, status="starting"):
        row = module.inspection.task_module.request()
        row.update({"ReplicationTaskArn": module.inspection.TASK_ARN, "Status": status})
        return row

    def test_fresh_report_accepted_and_stale_report_stops(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cutover-start-preflight.json"
            report = self.report()
            path.write_text(json.dumps(report))
            path.chmod(0o600)
            self.assertEqual(module.fresh_report(Path(directory))["task_status"], "ready")
            report["checked_at_utc"] = (datetime.now(timezone.utc) - timedelta(minutes=3)).isoformat()
            path.write_text(json.dumps(report))
            with self.assertRaises(module.runtime.SafetyStop):
                module.fresh_report(Path(directory))

    def test_only_first_start_api_called_and_cli_retries_disabled(self):
        def aws(*args, **kwargs):
            self.assertEqual(args, ("dms", "start-replication-task"))
            self.assertEqual(os.environ["AWS_MAX_ATTEMPTS"], "1")
            self.assertEqual(kwargs["body"], {"ReplicationTaskArn": module.inspection.TASK_ARN,
                                              "StartReplicationTaskType": "start-replication"})
            return {"ReplicationTask": self.row()}
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"AWS_MAX_ATTEMPTS": "7"}):
                with patch.object(module.runtime, "aws", side_effect=aws) as call:
                    module.start_once(Path(directory), self.report())
                    self.assertEqual(call.call_count, 1)
                self.assertEqual(os.environ["AWS_MAX_ATTEMPTS"], "7")
            record = json.loads((Path(directory) / "cutover-dms-start-record.json").read_text())
            self.assertEqual(record["phase"], "start_accepted")

    def test_duplicate_request_record_blocks_api(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "cutover-dms-start-record.json").touch()
            with patch.object(module.runtime, "aws") as aws:
                with self.assertRaises(FileExistsError):
                    module.start_once(Path(directory), self.report())
                aws.assert_not_called()

    def test_lost_start_response_never_retries_and_preserves_intent(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(module.runtime, "aws", side_effect=RuntimeError("synthetic lost response")) as aws:
                with self.assertRaises(RuntimeError):
                    module.start_once(Path(directory), self.report())
                self.assertEqual(aws.call_count, 1)
            record = json.loads((Path(directory) / "cutover-dms-start-record.json").read_text())
            self.assertEqual(record["phase"], "start_result_unknown")

    def test_running_task_stops_before_start(self):
        with patch.object(module.runtime, "aws", return_value={"ReplicationTasks": [self.row("running")]}) as aws:
            with self.assertRaises(module.runtime.SafetyStop):
                module.final_ready_check(self.report())
            self.assertEqual(aws.call_args.args[:2], ("dms", "describe-replication-tasks"))
            self.assertEqual(aws.call_count, 1)

    def test_secret_version_change_stops_before_start(self):
        with patch.object(module.runtime, "aws", return_value={"ReplicationTasks": [self.row("ready")]}) as aws:
            with patch.object(module.inspection.access, "endpoint", return_value={}):
                with patch.object(module.inspection.task_module, "check_endpoint"):
                    with patch.object(module.inspection, "check_secret_host",
                                      return_value={"host_verified": True, "version_id": "changed"}):
                        with self.assertRaises(module.runtime.SafetyStop):
                            module.final_ready_check(self.report())
            self.assertEqual(aws.call_count, 1)

    def test_refresh_archives_old_evidence_before_running_read_inspection(self):
        with tempfile.TemporaryDirectory() as directory:
            report = self.report()
            (Path(directory) / "cutover-start-preflight.json").write_text(json.dumps(report))
            with patch.object(module.subprocess, "run", return_value=Mock(returncode=0)) as run:
                module.refresh_preflight(Path(directory), 13306)
            archives = list(Path(directory).glob("cutover-preflight-archive-*.json"))
            self.assertEqual(len(archives), 1)
            self.assertEqual(json.loads(archives[0].read_text())["report"], report)
            self.assertIn("--approved-source-catalog-read", run.call_args.args[0])

    def test_missing_any_confirmation_blocks_before_directory_or_aws(self):
        with patch("sys.argv", ["start", "--directory", "/unused", "--approved-full-load-cdc"]):
            with patch.object(module.runtime, "protected_directory") as directory:
                with self.assertRaises(module.runtime.SafetyStop):
                    module.main()
                directory.assert_not_called()

    def test_connection_check_requires_both_exact_endpoints_successful(self):
        rows = [{"EndpointArn": arn, "Status": "successful"} for arn in
                (module.inspection.access.SOURCE_ENDPOINT, module.inspection.access.TARGET_ENDPOINT)]
        with patch.object(module.runtime, "aws", return_value={"Connections": rows}):
            module.check_connections()
            rows[0]["Status"] = "testing"
            with self.assertRaises(module.runtime.SafetyStop):
                module.check_connections()

    def test_failed_intent_preserved_as_archive(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            record = folder / "cutover-dms-start-record.json"
            state = {"task_arn": module.inspection.TASK_ARN,
                     "phase": "start_result_unknown", "start_type": "start-replication"}
            record.write_text(json.dumps(state))
            record.chmod(0o600)
            module.archive_failed_attempt(folder)
            self.assertFalse(record.exists())
            archives = list(folder.glob("cutover-dms-failed-start-*.json"))
            self.assertEqual(len(archives), 1)
            self.assertEqual(json.loads(archives[0].read_text()), state)

    def test_accepted_intent_never_archived_for_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            record = Path(directory) / "cutover-dms-start-record.json"
            record.write_text(json.dumps({"task_arn": module.inspection.TASK_ARN,
                                          "phase": "start_accepted", "start_type": "start-replication"}))
            record.chmod(0o600)
            with self.assertRaises(module.runtime.SafetyStop):
                module.archive_failed_attempt(Path(directory))
            self.assertTrue(record.exists())


if __name__ == "__main__":
    unittest.main()
