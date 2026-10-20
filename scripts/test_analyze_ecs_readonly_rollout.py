"""합성 로그·요약만 사용. AWS·k6 호출 없음."""
from datetime import timedelta
import importlib.util
import json
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("ecs_analysis", Path(__file__).with_name("analyze-ecs-readonly-rollout.py"))
analysis = importlib.util.module_from_spec(spec)
spec.loader.exec_module(analysis)
RUN = "20261017T000000Z-ecs-readonly-baseline-1"


class AnalysisTest(unittest.TestCase):
    def fixtures(self):
        lines = []
        for second in (1, 2, 3):
            row = {"kind": "ecs_read_request", "runId": RUN, "startedAt": f"2026-10-17T00:00:0{second}.000Z",
                   "finishedAt": f"2026-10-17T00:00:0{second}.100Z", "status": 200, "valid": True,
                   "durationMs": 100, "transportErrorCode": None}
            lines.append("level=info msg=" + json.dumps(json.dumps(row)) + " source=console")
        requests = analysis.requests_from_log("\n".join(lines), RUN)
        summary = {"metrics": {key: {"count": 3} for key in ("ecs_read_requests", "http_reqs", "iterations")}}
        summary["metrics"].update({"ecs_read_errors": {"value": 0}, "http_req_failed": {"value": 0},
                                   "dropped_iterations": {"count": 0}, "http_req_duration": {"avg": 100}})
        return requests, summary

    def evaluate(self, requests, summary):
        return analysis.analyze(requests, summary, "k6_exit_code=0\n",
                                analysis.utc("2026-10-17T00:00:02Z"), analysis.utc("2026-10-17T00:00:03Z"))

    def test_exact_boundaries(self):
        requests, summary = self.fixtures()
        result = self.evaluate(requests, summary)
        self.assertTrue(result["read_only_rollout_check_passed"])
        self.assertEqual({key: value["requests"] for key, value in result["phases"].items()}, {"before": 1, "during": 1, "after": 1})

    def test_same_status_invalid_body_fails(self):
        requests, summary = self.fixtures()
        requests[1]["valid"] = False
        self.assertFalse(self.evaluate(requests, summary)["read_only_rollout_check_passed"])

    def test_counter_mismatch_rejected(self):
        requests, summary = self.fixtures()
        summary["metrics"]["http_reqs"]["count"] = 4
        with self.assertRaises(ValueError):
            self.evaluate(requests, summary)

    def test_dropped_iterations_fail(self):
        requests, summary = self.fixtures()
        summary["metrics"]["dropped_iterations"]["count"] = 1
        self.assertFalse(self.evaluate(requests, summary)["read_only_rollout_check_passed"])

    def test_missing_phase_fails(self):
        requests, summary = self.fixtures()
        for request in requests:
            request["start"] += timedelta(minutes=1)
        self.assertFalse(self.evaluate(requests, summary)["read_only_rollout_check_passed"])

    def test_interpolated_percentile(self):
        self.assertAlmostEqual(analysis.percentile([0, 10], 0.95), 9.5)

    def test_wrong_run_rejected(self):
        row = {"kind": "ecs_read_request", "runId": "wrong"}
        with self.assertRaises(ValueError):
            analysis.requests_from_log("msg=" + json.dumps(json.dumps(row)), RUN)

    def test_summary_metric_is_not_request_log(self):
        row = {"kind": "ecs_read_request", "runId": RUN,
               "startedAt": "2026-10-17T00:00:01Z", "finishedAt": "2026-10-17T00:00:02Z",
               "status": 200, "valid": True, "durationMs": 100, "transportErrorCode": None}
        text = ("level=info msg=" + json.dumps(json.dumps(row)) + " source=console\n"
                "    ecs_read_requests\n"
                "    ecs_read_requests..............: 1201   1.000801/s\n")
        self.assertEqual(len(analysis.requests_from_log(text, RUN)), 1)

    def test_malformed_actual_request_still_rejected(self):
        with self.assertRaisesRegex(ValueError, "요청 로그 형식 불일치"):
            analysis.requests_from_log('kind=ecs_read_request truncated', RUN)


if __name__ == "__main__":
    unittest.main()
