#!/usr/bin/env python3
"""종료된 k6 Artifact만 분석. AWS 호출·부하 생성·서비스 변경 없음."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import statistics

ROOT = Path(__file__).resolve().parents[1]


def utc(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("시각에 UTC offset 필요")
    return parsed.astimezone(timezone.utc)


def percentile(values, quantile):
    ordered = sorted(values)
    if not ordered:
        return None
    position = (len(ordered) - 1) * quantile
    lower, upper = math.floor(position), math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def requests_from_log(text, run_id):
    requests = []
    for line in text.splitlines():
        # 최종 요약의 복수형 지표 ecs_read_requests는 개별 JSON 요청이 아님.
        # 실제 요청 표식이 있으나 깨진 로그는 계속 거절하여 누락 은폐 방지.
        if not re.search(r'\becs_read_request\b', line):
            continue
        match = re.search(r'msg=("(?:\\.|[^"\\])*")', line)
        if not match:
            raise ValueError("요청 로그 형식 불일치")
        request = json.loads(json.loads(match.group(1)))
        if request.get("kind") != "ecs_read_request" or request.get("runId") != run_id:
            raise ValueError("요청 RunID 또는 kind 불일치")
        start, finish = utc(request["startedAt"]), utc(request["finishedAt"])
        duration = float(request["durationMs"])
        if finish < start or not math.isfinite(duration) or duration < 0:
            raise ValueError("요청 시각·지연 값 불일치")
        request.update(start=start, finish=finish, duration=duration)
        requests.append(request)
    if not requests:
        raise ValueError("요청 로그 없음")
    return sorted(requests, key=lambda item: item["start"])


def summarize(requests):
    durations = [item["duration"] for item in requests]
    successes = sum(item["status"] == 200 and item["valid"] is True and not item.get("transportErrorCode") for item in requests)
    return {
        "requests": len(requests), "successes": successes, "errors": len(requests) - successes,
        "http_statuses": dict(Counter(str(item["status"]) for item in requests)),
        "http_duration_ms": {
            "min": min(durations) if durations else None,
            "avg": statistics.mean(durations) if durations else None,
            "p50": percentile(durations, 0.5), "p95": percentile(durations, 0.95),
            "p99": percentile(durations, 0.99), "max": max(durations) if durations else None,
        },
    }


def analyze(requests, summary, metadata, deploy_start, deploy_complete):
    if deploy_complete <= deploy_start:
        raise ValueError("배포 완료가 시작보다 빨라서는 안 됨")
    if "k6_exit_code=" not in metadata or not summary:
        raise ValueError("k6 최종 종료 기록 없음: 실행 중일 수 있음")
    exit_match = re.search(r"(?m)^k6_exit_code=(\d+)$", metadata)
    if not exit_match:
        raise ValueError("최종 종료 코드 형식 불일치")
    exit_code = int(exit_match.group(1))
    metrics = summary["metrics"]
    counter = metrics["ecs_read_requests"]["count"]
    if counter != len(requests) or metrics["http_reqs"]["count"] != len(requests) or metrics["iterations"]["count"] != len(requests):
        raise ValueError("요청 로그와 k6 최종 요청 수 불일치")
    phases = {
        "before": [item for item in requests if item["start"] < deploy_start],
        "during": [item for item in requests if deploy_start <= item["start"] < deploy_complete],
        "after": [item for item in requests if item["start"] >= deploy_complete],
    }
    phase_results = {name: summarize(items) for name, items in phases.items()}
    total = summarize(requests)
    passed = (
        exit_code == 0 and total["errors"] == 0 and
        metrics["ecs_read_errors"]["value"] == 0 and metrics["http_req_failed"]["value"] == 0 and
        metrics["dropped_iterations"]["count"] == 0 and
        all(items for items in phases.values())
    )
    gaps = [(right["start"] - left["start"]).total_seconds() for left, right in zip(requests, requests[1:])]
    return {
        "read_only_rollout_check_passed": passed,
        "k6_exit_code": exit_code,
        "deployment_start_utc": deploy_start.isoformat(),
        "deployment_complete_utc": deploy_complete.isoformat(),
        "deployment_window_seconds": (deploy_complete - deploy_start).total_seconds(),
        "phase_assignment": "request startedAt; [start,complete) is during",
        "first_request_utc": requests[0]["start"].isoformat(),
        "last_request_finished_utc": max(item["finish"] for item in requests).isoformat(),
        "max_request_start_gap_seconds": max(gaps) if gaps else None,
        "dropped_iterations": metrics["dropped_iterations"]["count"],
        "total": total, "phases": phase_results,
        "k6_http_duration_ms": metrics["http_req_duration"],
        "percentile_method": "linear interpolation at (n-1)*q; phase statistics from request logs",
        "limitations": [
            "상품 목록 1rps 순수 읽기 표본만 검증",
            "기존 BE 직접 주소 경로이며 FE 중계·인증·쓰기는 부하에 포함되지 않음",
            "배포 기간은 서비스 중단 시간이 아님; 모든 트래픽의 무중단 보장 아님",
            "실패 후보 자동 롤백·수동 복귀 성공은 별도 검증 필요",
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--deploy-start", required=True)
    parser.add_argument("--deploy-complete", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"\d{8}T\d{6}Z-ecs-readonly-baseline-\d+", args.run_id):
        raise ValueError("baseline RunID 형식 불일치")
    directory = ROOT / "load-tests/results" / args.run_id
    log = Path("/private/tmp") / (args.run_id + ".log")
    metadata = directory / "metadata.txt"
    summary = directory / "k6-summary.json"
    if not summary.is_file() or "k6_exit_code=" not in metadata.read_text():
        raise ValueError("시험 종료 전: k6-summary/최종 종료 기록 생성 후 재실행")
    result = analyze(requests_from_log(log.read_text(), args.run_id), json.loads(summary.read_text()),
                     metadata.read_text(), utc(args.deploy_start), utc(args.deploy_complete))
    result["run_id"] = args.run_id
    result["source_sha256"] = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in (log, metadata, summary)}
    result["analyzed_utc"] = datetime.now(timezone.utc).isoformat()
    target = directory / ("ecs-rollout-analysis-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json")
    with target.open("x") as stream:
        json.dump(result, stream, indent=2, ensure_ascii=False)
        stream.write("\n")
    print(json.dumps(result, indent=2, ensure_ascii=False))
    print("ANALYSIS_FILE=" + str(target))
    if not result["read_only_rollout_check_passed"]:
        raise SystemExit(2)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError) as error:
        raise SystemExit("STOP: " + str(error))
