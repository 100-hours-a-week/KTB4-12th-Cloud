#!/usr/bin/env python3
"""Mac: 승인된 V1 시험 로그인1회·선물1건. 자동 재시도/가입/전환 없음."""
import argparse
from datetime import datetime, timezone
import getpass
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import time
import uuid


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


loaded = load("loaded", "inspect-cutover-loaded-target.py")
inspection = load("inspection", "inspect-cutover-start.py")
login = load("login", "prepare-v1-infra-write-login.py")
runtime = loaded.runtime
TASK_ARN = inspection.TASK_ARN
RECORD = "cutover-cdc-canary-record.json"


def now():
    return datetime.now(timezone.utc).isoformat()


def check_task():
    rows = runtime.aws("dms", "describe-replication-tasks", "--filters",
                       "Name=replication-task-arn,Values=" + TASK_ARN)["ReplicationTasks"]
    runtime.require(len(rows) == 1, "새 Task 식별 실패")
    row = rows[0]
    expected = inspection.task_module.request()
    runtime.require(row.get("ReplicationTaskArn") == TASK_ARN and row.get("Status") == "running"
                    and not row.get("LastFailureMessage"), "새 Task 정상 running 필요")
    for key in ("SourceEndpointArn", "TargetEndpointArn", "ReplicationInstanceArn", "MigrationType"):
        runtime.require(row[key] == expected[key], "Task 대상 변경")
    runtime.require(json.loads(row["TableMappings"]) == inspection.task_module.mappings(), "Task mapping 변경")
    stats = row.get("ReplicationTaskStats", {})
    runtime.require(stats.get("FullLoadProgressPercent") == 100 and stats.get("TablesLoaded") == 16
                    and stats.get("TablesErrored") == 0, "초기 적재 완료 상태 변경")


def check_baseline(result):
    stocks = {row["id"]: row["quantity"] for row in result["target_stock"]}
    runtime.require(result["target_schema"] == loaded.access.TARGET_SCHEMA
                    and result["gift_rows"] == 5096 and result["latest_gift_id"] == 5096
                    and result["distinct_nonnull_gift_keys"] == 5096
                    and stocks.get(4) == 13, "승인된 최신 Source/Target 기준 불일치: 쓰기 중단")


def read_baseline(directory, port):
    check_task()
    master = loaded.access.helper.management_credentials()
    with runtime.connect_db(directory, port, master["username"], master["password"]) as connection:
        with connection.cursor() as cursor:
            result = loaded.inspect(cursor)
    check_baseline(result)
    return result


def post(path, payload, expected_status, token=None, key=None):
    runtime.require(path in ("/api/auth/login", "/api/gifts"), "허용하지 않은 API")
    # 비밀번호·Bearer는 argv/파일 대신 curl 표준입력 설정으로만 전달.
    config = ["header = " + json.dumps("Content-Type: application/json"),
              "data-binary = " + json.dumps(json.dumps(payload))]
    if token is not None:
        runtime.require(re.fullmatch(r"[A-Za-z0-9_.-]+", token) is not None, "토큰 형식 오류")
        config.append("header = " + json.dumps("Authorization: Bearer " + token))
    if key is not None:
        runtime.require(str(uuid.UUID(key)) == key, "멱등키 형식 오류")
        config.append("header = " + json.dumps("Idempotency-Key: " + key))
    response = subprocess.run([
        "curl", "--disable", "--silent", "--show-error", "--connect-timeout", "5", "--max-time", "15",
        "--retry", "0", "--proto", "=https", "--request", "POST", "--config", "-",
        "--write-out", "\n%{http_code}", login.BASE_URL + path,
    ], input="\n".join(config) + "\n", capture_output=True, text=True, timeout=25)
    runtime.require(response.returncode == 0, "API 연결 실패: 처리 여부 불명확·재시도 금지")
    raw, status = response.stdout.rsplit("\n", 1)
    runtime.require(status == str(expected_status), "API HTTP=" + status + ": 응답 원문 비공개·재시도 금지")
    result = json.loads(raw)
    runtime.require(result.get("error") is None, "업무 응답 오류: 재시도 금지")
    return result


def exclusive_record(directory, state):
    descriptor = os.open(directory / RECORD, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(state, stream)
        stream.flush()
        os.fsync(stream.fileno())


def run_canary(directory, port, password, baseline):
    key = str(uuid.uuid4())
    state = {"task_arn": TASK_ARN, "target_schema": loaded.access.TARGET_SCHEMA,
             "request_id": key, "sender_id": 524, "recipient_id": 577,
             "product_id": 4, "quantity": 1, "unit_price": 22900,
             "before_gifts": baseline["gift_rows"], "before_stock": 13,
             "phase": "login_requested", "login_requested_at_utc": now()}
    # 응답 유실·프로세스 종료 시에도 요청을 추가로 보내지 않도록 첫 HTTP 전에 intent 기록.
    exclusive_record(directory, state)
    response = post("/api/auth/login", {"email": login.EMAIL, "password": password}, 200)
    token, _ = login.login_data(response, time.time())
    state.update(phase="login_succeeded", login_completed_at_utc=now())
    runtime.secure_write(directory / RECORD, state)
    print("V1_CANARY_LOGIN_HTTP=200 TEST_USER_ID_VERIFIED=524", flush=True)
    # 비밀번호 입력/로그인 사이 대기가 길어졌거나 다른 쓰기가 생긴 경우 선물 전 중단.
    read_baseline(directory, port)
    state.update(phase="gift_requested", gift_requested_at_utc=now(),
                 gift_requested_monotonic_ns=time.monotonic_ns())
    runtime.secure_write(directory / RECORD, state)
    response = post("/api/gifts", {"productId": 4, "expectedUnitPrice": 22900,
                                   "recipientUserId": 577, "quantity": 1}, 201, token, key)
    gift_id = response.get("data", {}).get("gift", {}).get("giftId")
    runtime.require(type(gift_id) is int and gift_id > 5096, "선물 응답 ID 확인 실패: 재요청 금지")
    state.update(phase="gift_http_succeeded", gift_id=gift_id,
                 gift_http_completed_at_utc=now(), gift_http_completed_monotonic_ns=time.monotonic_ns())
    runtime.secure_write(directory / RECORD, state)
    print("V1_CANARY_GIFT_HTTP=201 GIFT_ID=" + str(gift_id))
    print("CANARY_RECORD_FILE=" + str(directory / RECORD))
    print("LOGIN_REQUESTS=1 GIFT_REQUESTS=1 NO_RETRY_NO_SIGNUP_NO_TRAFFIC_SWITCH")
    print("SOURCE_COMMIT_AND_TARGET_CDC_VERIFICATION_PENDING")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True)
    parser.add_argument("--local-port", type=int, default=13306)
    parser.add_argument("--approved-login-and-one-gift", action="store_true")
    parser.add_argument("--confirmed-source-gifts-5096-stock-13", action="store_true")
    args = parser.parse_args()
    runtime.require(args.approved_login_and_one_gift and args.confirmed_source_gifts_5096_stock_13,
                    "로그인1회·선물1건 승인 및 최신 Source 기준 확인 필요")
    runtime.require(1 <= args.local_port <= 65535, "포트 범위 오류")
    directory = runtime.protected_directory(args.directory)
    record = directory / RECORD
    runtime.require(not record.exists() and not record.is_symlink(), "기존 canary 요청 기록 존재: 재실행 금지")
    baseline = read_baseline(directory, args.local_port)
    print("DMS_RUNNING_TARGET_BASELINE_5096_STOCK_13_VERIFIED", flush=True)
    password = getpass.getpass("loadtest001 시험 계정 비밀번호(표시되지 않음): ")
    runtime.require(bool(password), "비밀번호 미입력: HTTP 요청 없음")
    # 입력 대기 중 바뀐 데이터가 있으면 첫 로그인 전에도 중단.
    baseline = read_baseline(directory, args.local_port)
    run_canary(directory, args.local_port, password, baseline)


if __name__ == "__main__":
    try:
        main()
    except (EOFError, KeyboardInterrupt):
        raise SystemExit("STOP: 입력 취소; 요청 기록이 있으면 상태 확인·재실행 금지")
    except Exception as error:
        detail = str(error) if isinstance(error, runtime.SafetyStop) else type(error).__name__
        raise SystemExit("STOP: " + detail + "; canary 기록 보존·재실행/삭제 금지")
