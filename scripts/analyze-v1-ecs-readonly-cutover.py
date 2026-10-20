#!/usr/bin/env python3
"""종료된 독립 읽기 전환 시험의 요청·k6·제어 이벤트를 로컬 대조."""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import re
from collections import Counter

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("common", Path(__file__).with_name("analyze-ecs-readonly-rollout.py"))
common = importlib.util.module_from_spec(spec)
spec.loader.exec_module(common)


def require(ok, message):
    if not ok:
        raise ValueError(message)


def analyze(requests, summary, metadata, events):
    completed = [event for event in events if "sequence_completed_utc" in event]
    require(len(completed) == 1 and completed[0]["k6_exit_code"] == 0, "제어 순서 완료·종료 코드 확인 필요")
    require(re.search(r"(?m)^k6_exit_code=0$", metadata), "k6 정상 종료 기록 필요")

    def event_time(target, key):
        matching = [event for event in events if event.get("target") == target and key in event]
        require(len(matching) == 1, "전환/복귀 이벤트 누락 또는 중복")
        return common.utc(matching[0][key])

    switch_start = event_time("ecs", "requested_utc")
    switch_verified = event_time("ecs", "verified_utc")
    return_start = event_time("v1", "requested_utc")
    return_verified = event_time("v1", "verified_utc")
    require(switch_start < switch_verified < return_start < return_verified, "제어 이벤트 시각 순서 불일치")
    metrics = summary["metrics"]
    count = len(requests)
    require(all(metrics[name]["count"] == count for name in ("ecs_read_requests", "http_reqs", "iterations")),
            "요청 원문과 k6 요청 수 불일치")
    phases = {
        "v1_before": [r for r in requests if r["start"] < switch_start],
        "switch_to_ecs": [r for r in requests if switch_start <= r["start"] < switch_verified],
        "ecs_stable": [r for r in requests if switch_verified <= r["start"] < return_start],
        "return_to_v1": [r for r in requests if return_start <= r["start"] < return_verified],
        "v1_after": [r for r in requests if r["start"] >= return_verified],
    }
    phase_results = {}
    for name, items in phases.items():
        phase_results[name] = {**common.summarize(items),
                               "upstreams": dict(Counter(r.get("upstream") for r in items))}
    total = common.summarize(requests)
    transitions = []
    for request in requests:
        if not transitions or transitions[-1]["upstream"] != request.get("upstream"):
            transitions.append({"upstream": request.get("upstream"),
                                "first_request_started_utc": request["startedAt"],
                                "first_response_finished_utc": request["finishedAt"]})
    gaps = [(right["start"] - left["start"]).total_seconds() for left, right in zip(requests, requests[1:])]
    passed = (
        total["errors"] == 0 and metrics["ecs_read_errors"]["value"] == 0 and
        metrics["http_req_failed"]["value"] == 0 and metrics["dropped_iterations"]["count"] == 0 and
        (max(r["finish"] for r in requests) - requests[0]["start"]).total_seconds() >= 299 and
        count >= 299 and max(gaps, default=0) <= 2 and
        [item["upstream"] for item in transitions] == ["v1", "ecs", "v1"] and
        all(len(phases[name]) >= 30 and all(r.get("upstream") == upstream for r in phases[name])
            for name, upstream in (("v1_before", "v1"), ("ecs_stable", "ecs"), ("v1_after", "v1"))) and
        all(phases[name] for name in ("switch_to_ecs", "return_to_v1"))
    )
    return {
        "read_only_cutover_check_passed": passed,
        "k6_exit_code": 0, "dropped_iterations": metrics["dropped_iterations"]["count"],
        "total": total, "upstreams": dict(Counter(r.get("upstream") for r in requests)),
        "phases": phase_results, "observed_target_transitions": transitions,
        "first_request_utc": requests[0]["startedAt"],
        "last_response_utc": max(r["finish"] for r in requests).isoformat(),
        "max_request_start_gap_seconds": max(gaps) if gaps else None,
        "observed_request_window_seconds": (max(r["finish"] for r in requests) - requests[0]["start"]).total_seconds(),
        "control_timings": {
            "switch_requested_utc": switch_start.isoformat(), "switch_verified_utc": switch_verified.isoformat(),
            "switch_request_to_verification_seconds": (switch_verified - switch_start).total_seconds(),
            "return_requested_utc": return_start.isoformat(), "return_verified_utc": return_verified.isoformat(),
            "return_request_to_verification_seconds": (return_verified - return_start).total_seconds(),
        },
        "phase_assignment": "request startedAt; 제어 요청부터 로컬 검증까지 전환 구간",
        "percentile_method": "linear interpolation at (n-1)*q",
        "limitations": [
            "5분·상품 목록 1rps·익명 읽기 표본만 검증",
            "Mac SSM 터널·시험 프록시 지연 포함, 운영 공개 진입점·FE·인증·쓰기 제외",
            "동일 시험 RDS의 앱 실행 위치 전환/복귀이며 DB 마이그레이션·역동기화 시험 아님",
            "제어 시간에는 SSM 전달·폴링·검증 포함, 서비스 중단 시간 또는 순수 reload 시간이 아님",
            "응답 표식은 시험 프록시 설정 기준이며 모든 앱 인스턴스의 요청 추적을 대신하지 않음",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    require(re.fullmatch(r"\d{8}T\d{6}Z-v1-ecs-readonly-baseline-\d+", args.run_id), "Run ID 형식 불일치")
    directory = ROOT / "load-tests/results" / args.run_id
    paths = {"log": directory / "request-console.log", "summary": directory / "k6-summary.json",
             "metadata": directory / "metadata.txt", "events": directory / "cutover-events.jsonl"}
    events = [json.loads(line) for line in paths["events"].read_text().splitlines() if line]
    result = analyze(common.requests_from_log(paths["log"].read_text(), args.run_id),
                     json.loads(paths["summary"].read_text()), paths["metadata"].read_text(), events)
    result["run_id"] = args.run_id
    result["source_sha256"] = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths.values()}
    result["analyzed_utc"] = datetime.now(timezone.utc).isoformat()
    target = directory / ("v1-ecs-cutover-analysis-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json")
    with target.open("x") as stream:
        json.dump(result, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    print("ANALYSIS_FILE=" + str(target))
    if not result["read_only_cutover_check_passed"]:
        raise SystemExit(2)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError) as error:
        raise SystemExit("STOP: " + str(error))
