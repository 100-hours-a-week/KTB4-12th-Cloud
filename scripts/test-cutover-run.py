#!/usr/bin/env python3
"""전환 순서·실패 시 Writer 경계·정합성 검사를 로컬 모의 검증. AWS 호출 없음."""
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


run = load("cutover_run", ROOT / "operations/staging/v1-candidate/run-cutover.py")
prepare = load("cutover_run_prepare", Path(__file__).with_name("prepare-cutover-run.py"))


class Tests(unittest.TestCase):
    def rehearsal(self, directory):
        obj = run.Run(SimpleNamespace(), b"original")
        obj.work = Path(directory)
        obj.db_id = "fixed-db-id"
        obj.plans = {k: k.encode() for k in ("source-freeze.conf", "target-freeze.conf", "target-open.conf")}
        obj.reload = MagicMock()
        obj.source = MagicMock(return_value="0")
        obj.fingerprints = MagicMock(return_value={n: "123" for n in run.TABLES})
        obj.orphans = MagicMock()
        obj.sample = MagicMock()
        obj.samples = [{"monotonic_ns": 1, "pair_ok": True} for _ in range(3)]
        return obj

    def execute_case(self, failure=None):
        with tempfile.TemporaryDirectory() as directory:
            obj = self.rehearsal(directory)
            marker = Path(directory) / "intent.json"
            def source(sql):
                if sql.startswith("SELECT @@GLOBAL"):
                    return "0\t0" if "recovery_source_route_old_workers_drained" in [x["event"] for x in obj.events] else "1\t1"
                return "0"
            obj.source.side_effect = source
            sequence = []
            def reload(value, name):
                sequence.append(name)
                obj.changed = True
                obj.event(name + "_old_workers_drained")
                if failure == name:
                    raise RuntimeError("INJECTED")
            obj.reload.side_effect = reload
            def aws(*args):
                if failure == "stop":
                    raise RuntimeError("AWS_STOP_FAILED_AccessDeniedException")
                return {}
            with patch.object(run, "MARKER", marker), patch.object(run, "task", return_value={"Status": "stopped"}), \
                 patch.object(run, "validation"), patch.object(run, "get", return_value=(503, b"")), \
                 patch.object(run, "read_check"), patch.object(run, "aws", side_effect=aws), \
                 patch.object(run.time, "sleep"), patch("builtins.print"):
                if failure:
                    with self.assertRaises(RuntimeError):
                        obj.execute()
                else:
                    obj.execute()
            return sequence, obj, json.loads((Path(directory) / "result.json").read_text())

    def test_success_orders_stop_before_target_open(self):
        sequence, obj, result = self.execute_case()
        self.assertEqual(sequence, ["source_freeze", "target_read_switch", "target_open"])
        events = [x["event"] for x in obj.events]
        self.assertLess(events.index("dms_stopped"), events.index("target_open_requested"))
        self.assertEqual(result["phase"], "control_complete_business_write_qa_pending")
        self.assertIsNone(result["first_business_write_seconds"])

    def test_stop_denied_restores_source(self):
        sequence, obj, result = self.execute_case("stop")
        self.assertNotIn("target_open", sequence)
        self.assertIn("recovery_source_route", sequence)
        self.assertTrue(any("super_read_only=OFF" in c.args[0] for c in obj.source.call_args_list))
        self.assertIn("source_service_restored_no_target_business_writer", [x["event"] for x in obj.events])

    def test_failure_after_target_open_never_unfences_source(self):
        sequence, obj, result = self.execute_case("target_open")
        self.assertEqual(sequence[-1], "recovery_target_freeze")
        self.assertFalse(any("super_read_only=OFF" in c.args[0] for c in obj.source.call_args_list))

    def test_failure_during_read_switch_can_restore_source(self):
        sequence, obj, result = self.execute_case("target_read_switch")
        self.assertEqual(sequence[-1], "recovery_source_route")
        self.assertNotIn("target_open", sequence)

    def test_null_checksum_rejected(self):
        obj = run.Run(None, b"")
        with self.assertRaisesRegex(RuntimeError, "INVALID_CHECKSUM"):
            obj.fingerprints(lambda sql: "seonjalal_develop.products\tNULL", run.SOURCE_SCHEMA)

    def test_missing_checksum_table_rejected(self):
        obj = run.Run(None, b"")
        with self.assertRaisesRegex(RuntimeError, "TABLE_MISSING"):
            obj.fingerprints(lambda sql: "seonjalal_develop.products\t123", run.SOURCE_SCHEMA)

    def test_password_only_in_stdin(self):
        obj = run.Run(None, b"")
        obj.password, obj.db_id, obj.ca_path = "not-a-real-secret", "db-id", "/tmp/public-ca.pem"
        with patch.object(run, "call", return_value=b"1") as call:
            self.assertEqual(obj.target("SELECT 1;"), "1")
        self.assertNotIn(obj.password, json.dumps(call.call_args.args[0]))
        self.assertEqual(call.call_args.kwargs["data"], b"not-a-real-secret\n")

    def test_request_scope(self):
        files = {"fe.py": b"pass", "run.py": b"pass", "original.conf": b"server {}"}
        request = prepare.build_request(files)
        self.assertEqual(request["InstanceIds"], ["i-0c6f5ea09b5e20859"])
        self.assertEqual(request["Parameters"]["executionTimeout"], ["600"])
        self.assertLessEqual(len(request["Comment"]), 100)
        self.assertNotIn("\n", request["Comment"])

    def test_source_restore_only_before_target_open(self):
        with tempfile.TemporaryDirectory() as directory:
            obj = self.rehearsal(directory)
            obj.changed, obj.fence_attempted = True, True
            obj.source.return_value = "0\t0"
            with patch.object(run, "read_check"), patch("builtins.print"):
                obj.recover()
            self.assertEqual(obj.reload.call_args.args[0], b"original")


if __name__ == "__main__":
    unittest.main()
