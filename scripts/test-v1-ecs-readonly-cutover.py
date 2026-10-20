"""외부 실행 없이 5분 시험 제어의 승인·대상·결과 기록 검사."""
import importlib.util
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location("cutover", Path(__file__).with_name("run-v1-ecs-readonly-cutover.py"))
cutover = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cutover)


class CutoverControlTest(unittest.TestCase):
    def setUp(self):
        cutover.OUTSTANDING_COMMANDS.clear()

    def test_no_approval_stops_before_aws(self):
        with patch("sys.argv", ["test"]), patch.object(cutover, "aws") as aws:
            with self.assertRaises(RuntimeError):
                cutover.main()
            aws.assert_not_called()

    def test_switch_only_targets_isolated_proxy_and_requires_remote_and_local_proof(self):
        results = [{"Command": {"CommandId": "test-id"}},
                   {"Status": "Success", "ResponseCode": 0,
                    "StandardOutputContent": "PROXY_TARGET=ecs\n"}]
        records = []
        with patch.object(cutover, "aws", side_effect=results) as aws, \
             patch.object(cutover, "probe") as probe:
            cutover.switch("ecs", records.append)
            args = aws.call_args_list[0].args
            self.assertIn(cutover.INSTANCE, args)
            self.assertIn("--parameters", args)
            self.assertIn("flock -w 5", args[-1])
            self.assertIn("proxy.py switch --target ecs", args[-1])
            probe.assert_called_once_with("ecs")
            self.assertIn("verified_utc", records[-1])
            self.assertFalse(cutover.OUTSTANDING_COMMANDS)

    def test_failed_switch_never_records_verified(self):
        records = []
        with patch.object(cutover, "aws", side_effect=[{"Command": {"CommandId": "failed"}},
                                                      {"Status": "Failed"}]):
            with self.assertRaises(RuntimeError):
                cutover.switch("ecs", records.append)
        self.assertFalse(any("verified_utc" in item for item in records))

    def test_load_must_be_running_before_delayed_switch(self):
        load = Mock()
        load.poll.return_value = 1
        with self.assertRaises(RuntimeError):
            cutover.wait_load(load, 1)

    def test_read_monitor_stops_without_waiting_for_45_seconds(self):
        load = Mock()
        load.poll.return_value = None
        monitor = Mock(side_effect=RuntimeError("SIMULATED_READ_ERROR"))
        with patch.object(cutover.time, "sleep") as sleep:
            with self.assertRaises(RuntimeError):
                cutover.wait_load(load, 45, monitor)
            monitor.assert_called_once()
            sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
