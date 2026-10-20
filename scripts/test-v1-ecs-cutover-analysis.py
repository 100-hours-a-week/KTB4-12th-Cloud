"""합성 요청으로 전환 표식·지표·최소 관측 조건의 로컬 회귀 검사."""
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("analysis", Path(__file__).with_name("analyze-v1-ecs-readonly-cutover.py"))
analysis = importlib.util.module_from_spec(spec)
spec.loader.exec_module(analysis)


class AnalysisTest(unittest.TestCase):
    def inputs(self):
        origin = datetime(2026, 1, 1, tzinfo=timezone.utc)
        requests = []
        for second in range(301):
            start = origin + timedelta(seconds=second)
            finish = start + timedelta(milliseconds=100)
            requests.append({"start": start, "finish": finish, "startedAt": start.isoformat(),
                             "finishedAt": finish.isoformat(), "duration": 100,
                             "status": 200, "valid": True, "transportErrorCode": None,
                             "upstream": "ecs" if 50 <= second < 100 else "v1"})
        events = [{"target": target, key: (origin + timedelta(seconds=second)).isoformat()}
                  for target, key, second in (("ecs", "requested_utc", 45), ("ecs", "verified_utc", 50),
                                              ("v1", "requested_utc", 95), ("v1", "verified_utc", 100))]
        events.append({"sequence_completed_utc": (origin + timedelta(seconds=302)).isoformat(), "k6_exit_code": 0})
        metrics = {name: {"count": 301} for name in ("ecs_read_requests", "http_reqs", "iterations")}
        metrics.update({name: {"value": 0} for name in ("ecs_read_errors", "http_req_failed")})
        metrics["dropped_iterations"] = {"count": 0}
        return requests, {"metrics": metrics}, "k6_exit_code=0\n", events

    def test_valid_sequence_passes(self):
        result = analysis.analyze(*self.inputs())
        self.assertTrue(result["read_only_cutover_check_passed"])
        self.assertEqual(result["total"]["requests"], 301)

    def test_no_ecs_responses_fail_even_when_control_claims_success(self):
        inputs = self.inputs()
        for request in inputs[0]:
            request["upstream"] = "v1"
        self.assertFalse(analysis.analyze(*inputs)["read_only_cutover_check_passed"])

    def test_request_error_prevents_pass(self):
        inputs = self.inputs()
        inputs[0][70]["valid"] = False
        inputs[0][70]["status"] = 0
        self.assertFalse(analysis.analyze(*inputs)["read_only_cutover_check_passed"])

    def test_missing_control_event_is_rejected(self):
        inputs = self.inputs()
        inputs[-1].pop(1)
        with self.assertRaises(ValueError):
            analysis.analyze(*inputs)

    def test_summary_mismatch_is_rejected(self):
        inputs = self.inputs()
        inputs[1]["metrics"]["http_reqs"]["count"] = 300
        with self.assertRaises(ValueError):
            analysis.analyze(*inputs)


if __name__ == "__main__":
    unittest.main()
