"""DMS 시작 API 없이 Source 카탈로그·실제 Task·승인 경계 모의 검사."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "start_inspection", Path(__file__).with_name("inspect-cutover-start.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class StartInspectionTest(unittest.TestCase):
    def catalog(self):
        return {"catalog": [["seonjalal_develop", name, "BASE TABLE", "InnoDB"]
                            for name in module.access.TABLES],
                "binlog": ["1", "ROW", "FULL", "0", "0"], "scheduler_flags": ["false", "false"],
                "source_trigger_count": 0, "source_event_count": 0, "open_transactions_at_sample": 0}

    def task(self):
        row = module.task_module.request()
        row.update({"ReplicationTaskArn": module.TASK_ARN, "Status": "ready"})
        return row

    def test_exact_source_catalog_passes(self):
        module.check_catalog(self.catalog())

    def test_wildcard_expansion_and_views_are_rejected(self):
        for extra in (["seonjalalXdevelop", "users", "BASE TABLE", "InnoDB"],
                      ["seonjalal_develop", "extra", "BASE TABLE", "InnoDB"],
                      ["seonjalal_develop", "users_view", "VIEW", ""]):
            data = self.catalog()
            data["catalog"].append(extra)
            with self.assertRaises(module.runtime.SafetyStop):
                module.check_catalog(data)

    def test_binlog_or_background_writers_change_stops(self):
        for key, value in (("binlog", ["1", "ROW", "MINIMAL", "0", "0"]),
                           ("scheduler_flags", ["true", "false"]), ("source_event_count", 1)):
            data = self.catalog()
            data[key] = value
            with self.assertRaises(module.runtime.SafetyStop):
                module.check_catalog(data)

    def test_actual_task_ready_and_settings_pass(self):
        module.check_task(self.task())

    def test_running_task_and_changed_target_are_rejected(self):
        for key, value in (("Status", "running"), ("TargetEndpointArn", "other")):
            row = self.task()
            row[key] = value
            with self.assertRaises(module.runtime.SafetyStop):
                module.check_task(row)

    def test_changed_task_initialization_rejected(self):
        row = self.task()
        settings = json.loads(row["ReplicationTaskSettings"])
        settings["FullLoadSettings"]["TargetTablePrepMode"] = "TRUNCATE_BEFORE_LOAD"
        row["ReplicationTaskSettings"] = json.dumps(settings)
        with self.assertRaises(module.runtime.SafetyStop):
            module.check_task(row)

    def test_added_default_log_component_allowed_without_relaxing_required_component(self):
        settings = copy.deepcopy(module.task_module.settings())
        settings["Logging"]["LogComponents"].append({"Id": "TASK_MANAGER", "Severity": "LOGGER_SEVERITY_DEFAULT"})
        module.require_settings(settings, module.task_module.settings())
        settings["Logging"]["LogComponents"][0]["Severity"] = "LOGGER_SEVERITY_DEBUG"
        with self.assertRaises(module.runtime.SafetyStop):
            module.require_settings(settings, module.task_module.settings())

    def test_remote_code_compiles_and_no_mutation_command(self):
        code = module.remote_program()
        compile(code, "source_catalog", "exec")
        self.assertIn("information_schema.tables", code)
        self.assertIn("binlog_row_image", code)
        for forbidden in ("CREATE TABLE", "INSERT INTO", "UPDATE ", "DELETE FROM", "SET GLOBAL", "docker restart"):
            self.assertNotIn(forbidden, code)

    def test_missing_read_approval_stops_before_aws(self):
        with patch("sys.argv", ["inspect", "--directory", "/unused"]):
            with patch.object(module.access.helper, "management_credentials") as credentials:
                with self.assertRaises(module.runtime.SafetyStop):
                    module.main()
                credentials.assert_not_called()


if __name__ == "__main__":
    unittest.main()
