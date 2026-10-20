#!/usr/bin/env python3
"""Mac: 승인된 새 시험 Task 최초 시작만 수행. 자동 재시도/재적재/트래픽 전환 없음."""
import argparse
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import time
import uuid

spec = importlib.util.spec_from_file_location(
    "start_inspection", Path(__file__).with_name("inspect-cutover-start.py"))
inspection = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inspection)
runtime = inspection.runtime


def refresh_preflight(directory, port):
    # 이전 승인/조회 증거를 지우지 않고 보호 파일에 보존.
    previous = directory / "cutover-start-preflight.json"
    if previous.exists():
        runtime.require(not previous.is_symlink(), "기존 점검 기록 심볼릭 링크 금지")
        archive = {"report": json.loads(previous.read_text())}
        command = directory / "cutover-source-catalog-command.json"
        if command.exists():
            runtime.require(not command.is_symlink(), "SSM 기록 심볼릭 링크 금지")
            archive["source_read_command"] = json.loads(command.read_text())
        runtime.secure_write(directory / ("cutover-preflight-archive-" + uuid.uuid4().hex + ".json"), archive)
    print("REFRESHING_READONLY_START_PREFLIGHT", flush=True)
    result = subprocess.run([sys.executable, str(Path(__file__).with_name("inspect-cutover-start.py")),
                             "--directory", str(directory), "--local-port", str(port),
                             "--approved-source-catalog-read"], timeout=240)
    runtime.require(result.returncode == 0, "최신 읽기 검사 실패: 복제 시작하지 않음")


def fresh_report(directory):
    path = directory / "cutover-start-preflight.json"
    info = path.lstat()
    runtime.require(not path.is_symlink() and info.st_uid == os.getuid()
                    and stat.S_IMODE(info.st_mode) == 0o600, "점검 기록 권한/소유자 확인 필요")
    report = json.loads(path.read_text())
    runtime.require(report["task_arn"] == inspection.TASK_ARN and report["task_status"] == "ready"
                    and report["settings_verified"] is True and report["replication_started"] is False
                    and report["source_catalog_tables"] == 16 and report["target_tables"] == 16
                    and report["target_rows"] == 0 and report["source_ssl"] == "none"
                    and report["target_ssl"] == "verify-full", "최신 점검 기록 대상/상태 불일치")
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(report["checked_at_utc"])).total_seconds()
    runtime.require(-5 <= age <= 120, "점검 기록이 오래됐거나 시각 불일치: 시작 중단")
    inspection.check_catalog(report["source_catalog"])
    return report


def final_ready_check(report):
    rows = runtime.aws("dms", "describe-replication-tasks", "--filters",
                       "Name=replication-task-arn,Values=" + inspection.TASK_ARN)["ReplicationTasks"]
    runtime.require(len(rows) == 1, "새 작업 식별 실패")
    row = rows[0]
    inspection.check_task(row)
    runtime.require(not row.get("ReplicationTaskStartDate")
                    and not row.get("ReplicationTaskStats", {}).get("TablesLoaded", 0),
                    "이미 실행한 이력이 있는 작업: 최초 시작 금지")
    for label, arn, host, kind in (
        ("source", inspection.access.SOURCE_ENDPOINT, inspection.SOURCE_HOST, "SOURCE"),
        ("target", inspection.access.TARGET_ENDPOINT, runtime.DB_HOST, "TARGET"),
    ):
        endpoint = inspection.access.endpoint(arn, kind)
        inspection.task_module.check_endpoint(endpoint, kind)
        current = inspection.check_secret_host(endpoint, label, host)
        runtime.require(current == report["secret_versions"][label], "점검 이후 Secret 버전/대상 변경")


def check_connections():
    rows = runtime.aws("dms", "describe-connections", "--filters",
                       "Name=replication-instance-arn,Values=" +
                       inspection.task_module.request()["ReplicationInstanceArn"])["Connections"]
    for arn in (inspection.access.SOURCE_ENDPOINT, inspection.access.TARGET_ENDPOINT):
        matches = [row for row in rows if row.get("EndpointArn") == arn]
        runtime.require(len(matches) == 1 and matches[0].get("Status") == "successful",
                        "DMS Source/Target 연결 successful 확인 필요: 시작 중단")
    print("DMS_SOURCE_AND_TARGET_CONNECTIONS_SUCCESSFUL")


def archive_failed_attempt(directory):
    # 최신 ready/미시작·빈 Target·연결 성공 검사 이후에만 호출. 실패 이력 삭제 금지.
    record = directory / "cutover-dms-start-record.json"
    info = record.lstat()
    runtime.require(stat.S_ISREG(info.st_mode) and not record.is_symlink()
                    and info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o600,
                    "기존 시작 기록 권한/형식 확인 필요")
    state = json.loads(record.read_text())
    runtime.require(state.get("task_arn") == inspection.TASK_ARN
                    and state.get("phase") == "start_result_unknown"
                    and state.get("start_type") == "start-replication",
                    "보존 가능한 실패 요청 기록 아님: 시작 중단")
    record.rename(directory / ("cutover-dms-failed-start-" + uuid.uuid4().hex + ".json"))
    print("PREVIOUS_FAILED_START_RECORD_ARCHIVED")


def start_once(directory, report):
    record = directory / "cutover-dms-start-record.json"
    state = {"task_arn": inspection.TASK_ARN, "phase": "start_requested",
             "start_type": "start-replication", "preflight_checked_at_utc": report["checked_at_utc"],
             "requested_at_utc": datetime.now(timezone.utc).isoformat(),
             "requested_monotonic_ns": time.monotonic_ns(),
             "approved_scope": "test-data/source-TLS-exception/schema-freeze/target-DMS-only"}
    # 중복 터미널 실행도 같은 작업을 두 번 시작하지 못하도록 원자적 intent 생성.
    descriptor = os.open(record, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(state, stream)
        stream.flush()
        os.fsync(stream.fileno())
    print("DMS_FIRST_START_REQUESTED_AT_UTC=" + state["requested_at_utc"], flush=True)
    previous_attempts = os.environ.get("AWS_MAX_ATTEMPTS")
    try:
        # CLI 내부 재시도도 이번 Start 요청에 한해 차단. 기존 환경값은 finally에서 복원.
        os.environ["AWS_MAX_ATTEMPTS"] = "1"
        response = runtime.aws("dms", "start-replication-task", body={
            "ReplicationTaskArn": inspection.TASK_ARN, "StartReplicationTaskType": "start-replication"})
        row = response["ReplicationTask"]
        expected = inspection.task_module.request()
        runtime.require(row["ReplicationTaskArn"] == inspection.TASK_ARN
                        and row["Status"] in ("starting", "running"), "응답 상태 불명확: 실제 Task 조회 필요")
        for key in ("ReplicationTaskIdentifier", "SourceEndpointArn", "TargetEndpointArn",
                    "ReplicationInstanceArn", "MigrationType"):
            runtime.require(row[key] == expected[key], "시작 응답 대상 불일치")
        state.update({"phase": "start_accepted", "status": row["Status"],
                      "accepted_at_utc": datetime.now(timezone.utc).isoformat(),
                      "accepted_monotonic_ns": time.monotonic_ns()})
        runtime.secure_write(record, state)
    except Exception:
        # 응답 유실이어도 실제 복제가 시작됐을 수 있음. 재시도/Resume/Reload 금지.
        state["phase"] = "start_result_unknown"
        runtime.secure_write(record, state)
        raise
    finally:
        if previous_attempts is None:
            os.environ.pop("AWS_MAX_ATTEMPTS", None)
        else:
            os.environ["AWS_MAX_ATTEMPTS"] = previous_attempts
    print("DMS_FIRST_START_ACCEPTED")
    print("TASK_STATUS=" + state["status"])
    print("NO_SOURCE_FREEZE_OR_TRAFFIC_SWITCH_REQUESTED")
    print("FULL_LOAD_CDC_AND_DATA_VALIDATION_RESULTS_PENDING")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True)
    parser.add_argument("--local-port", type=int, default=13306)
    parser.add_argument("--approved-full-load-cdc", action="store_true")
    parser.add_argument("--confirmed-test-data-source-tls-exception", action="store_true")
    parser.add_argument("--confirmed-source-schema-freeze", action="store_true")
    parser.add_argument("--confirmed-target-no-business-writer", action="store_true")
    parser.add_argument("--approved-retry-after-connection-repair", action="store_true")
    args = parser.parse_args()
    runtime.require(all((args.approved_full_load_cdc, args.confirmed_test_data_source_tls_exception,
                         args.confirmed_source_schema_freeze, args.confirmed_target_no_business_writer)),
                    "시험 데이터/TLS 예외·schema 동결·Target 업무쓰기 금지·시작 승인 모두 필요")
    runtime.require(1 <= args.local_port <= 65535, "포트 범위 오류")
    directory = runtime.protected_directory(args.directory)
    previous = directory / "cutover-dms-start-record.json"
    has_previous = previous.exists() or previous.is_symlink()
    runtime.require(not has_previous or args.approved_retry_after_connection_repair,
                    "이전 시작 요청 기록 존재: 재실행 대신 실제 작업 상태 확인")
    runtime.require(not args.approved_retry_after_connection_repair or has_previous,
                    "재요청 승인에 대응하는 기존 실패 기록 없음")
    refresh_preflight(directory, args.local_port)
    report = fresh_report(directory)
    final_ready_check(report)
    check_connections()
    if has_previous:
        archive_failed_attempt(directory)
    start_once(directory, report)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        detail = str(error) if isinstance(error, runtime.SafetyStop) else type(error).__name__
        raise SystemExit("STOP: " + detail + "; 시작 표식 이후 오류면 새 Task 상태 확인·재실행 금지")
