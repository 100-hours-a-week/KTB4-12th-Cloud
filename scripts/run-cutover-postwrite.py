#!/usr/bin/env python3
"""Mac: 전환 후 로그인1회·선물1건 및 RDS 원장/재고 검증. 재시도·가입 없음."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import getpass
import importlib.util
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import time
import uuid

spec = importlib.util.spec_from_file_location("canary", Path(__file__).with_name("run-cutover-cdc-canary.py"))
canary = importlib.util.module_from_spec(spec)
spec.loader.exec_module(canary)
runtime = canary.runtime
SCHEMA = "seonjalal_cutover_20261019"
HOST = "seonjalal-v2-migration-rehearsal.cbqckkusw4ke.ap-northeast-2.rds.amazonaws.com"
COMMAND = "236b3960-871e-45e6-be25-aa1d9f97cd56"
RECORD = "cutover-postwrite-record.json"


def cutover_check():
    row = runtime.aws("ssm", "get-command-invocation", "--command-id", COMMAND,
                      "--instance-id", "i-0c6f5ea09b5e20859")
    runtime.require(row["Status"] == "Success" and row["ResponseCode"] == 0, "실제 전환 성공 기록 필요")
    lines = [line for line in row["StandardOutputContent"].splitlines() if line.startswith("CUTOVER_RESULT=")]
    runtime.require(len(lines) == 1, "전환 결과 형식 불일치")
    result = json.loads(lines[0].split("=", 1)[1])
    runtime.require(result["phase"] == "control_complete_business_write_qa_pending"
                    and result["target_schema"] == SCHEMA and result["read_probe_failed_pairs"] == 0,
                    "승인된 전환 결과와 다름")
    tasks = runtime.aws("dms", "describe-replication-tasks", "--without-settings", "--filters",
                        "Name=replication-task-arn,Values=" + canary.TASK_ARN)["ReplicationTasks"]
    runtime.require(len(tasks) == 1 and tasks[0]["Status"] == "stopped", "DMS 정지 상태 필요")
    return result


def listening(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.3):
            return True
    except OSError:
        return False


@contextmanager
def db_port():
    # 이미 열린 관리 터널은 재사용하고 종료하지 않음. 없으면 이 프로세스 소유 임시 세션만 사용.
    if listening(13306):
        yield 13306
        return
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    parameters = {"host": [HOST], "portNumber": ["3306"], "localPortNumber": [str(port)]}
    child = subprocess.Popen(["aws", "ssm", "start-session", "--profile", "seonjalal-production",
        "--region", "ap-northeast-2", "--target", "i-0c6f5ea09b5e20859", "--document-name",
        "AWS-StartPortForwardingSessionToRemoteHost", "--parameters", json.dumps(parameters)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        deadline = time.monotonic() + 30
        while not listening(port):
            runtime.require(child.poll() is None and time.monotonic() < deadline,
                            "임시 RDS 관리 터널 연결 실패; API 요청 없음")
            time.sleep(0.3)
        print("TEMPORARY_READONLY_DB_ACCESS_TUNNEL_READY", flush=True)
        yield port
    finally:
        # 자신이 시작한 정확한 프로세스 그룹만 종료. 기존 SSM/다른 터미널 영향 없음.
        if child.poll() is None:
            os.killpg(child.pid, signal.SIGTERM)
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait(timeout=5)


def connect(directory, port):
    master = canary.loaded.access.helper.management_credentials()
    return runtime.connect_db(directory, port, master["username"], master["password"])


def baseline(directory, port):
    cutover_check()
    with connect(directory, port) as connection, connection.cursor() as cursor:
        cursor.execute(f"SELECT COUNT(*),COUNT(DISTINCT idempotency_key),MAX(id) FROM `{SCHEMA}`.gift_histories")
        count, keys, latest = cursor.fetchone()
        cursor.execute(f"SELECT price,quantity FROM `{SCHEMA}`.products WHERE id=4 AND deleted_at IS NULL")
        product = cursor.fetchone()
        runtime.require(product is not None and int(product[0]) == 22900 and product[1] >= 1,
                        "시험 상품 가격/재고 불일치")
        runtime.require(count == keys and count >= 5097 and latest >= 5097, "시험 원장 기준 불일치")
        cursor.execute(f"SELECT COUNT(*) FROM `{SCHEMA}`.users WHERE id IN (524,577) "
                       "AND status='ACTIVE' AND deleted_at IS NULL")
        runtime.require(cursor.fetchone()[0] == 2, "시험 사용자 상태 불일치")
        cursor.execute(f"SELECT COUNT(*) FROM `{SCHEMA}`.friends WHERE user_id=524 AND friend_user_id=577 "
                       "AND deleted_at IS NULL")
        runtime.require(cursor.fetchone()[0] == 1, "시험 친구 관계 불일치")
    return {"gift_count": int(count), "last_gift_id": int(latest), "stock": int(product[1])}


def verify(cursor, state):
    key = uuid.UUID(state["request_id"]).bytes
    cursor.execute("START TRANSACTION READ ONLY")
    try:
        cursor.execute(f"SELECT id,sender_id,recipient_id,product_id,quantity,product_price_snapshot,status,"
                       f"HEX(idempotency_key),deleted_at IS NULL FROM `{SCHEMA}`.gift_histories "
                       "WHERE sender_id=524 AND idempotency_key=%s", (key,))
        rows = cursor.fetchall()
        runtime.require(len(rows) == 1, "새 RDS에 동일 멱등키 선물 1행 미확인; API 재요청 금지")
        row = rows[0]
        runtime.require(tuple(row[1:5]) == (524,577,4,1) and int(row[5]) == 22900
                        and row[6] == "COMPLETED" and row[7].upper() == key.hex().upper() and row[8] == 1
                        and row[0] > state["baseline"]["last_gift_id"], "새 RDS 선물 필드 불일치")
        if state.get("gift_id") is not None:
            runtime.require(row[0] == state["gift_id"], "HTTP 응답과 RDS 선물 ID 불일치")
        cursor.execute(f"SELECT COUNT(*),COUNT(DISTINCT idempotency_key) FROM `{SCHEMA}`.gift_histories")
        counts = cursor.fetchone()
        runtime.require(tuple(map(int, counts)) == (state["baseline"]["gift_count"] + 1,) * 2,
                        "원장 증가량/멱등키 개수 불일치")
        cursor.execute(f"SELECT quantity FROM `{SCHEMA}`.products WHERE id=4")
        stock = cursor.fetchone()[0]
        runtime.require(stock == state["baseline"]["stock"] - 1, "재고 감소량 불일치")
        return {"gift_id": row[0], "target_schema": SCHEMA, "gift_count": int(counts[0]),
                "product_4_stock": int(stock), "same_key_single_row": True,
                "gift_status": "COMPLETED", "verified_at_utc": datetime.now(timezone.utc).isoformat()}
    finally:
        cursor.execute("ROLLBACK")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True)
    parser.add_argument("--approved-post-cutover-login-and-one-gift", action="store_true")
    parser.add_argument("--verify-only", action="store_true")
    args = parser.parse_args()
    directory = runtime.protected_directory(args.directory)
    path = directory / RECORD
    if not args.verify_only:
        runtime.require(args.approved_post_cutover_login_and_one_gift, "로그인1회·선물1건 승인 필요")
        runtime.require(not path.exists() and not path.is_symlink(), "이전 요청 기록 존재; --verify-only만 사용")
    with db_port() as port:
        if args.verify_only:
            info = path.lstat()
            runtime.require(not path.is_symlink() and info.st_uid == os.getuid()
                            and info.st_mode & 0o777 == 0o600, "시험 기록 소유자/권한 불일치")
            state = json.loads(path.read_text())
            runtime.require(state.get("command_id") == COMMAND and state.get("target_schema") == SCHEMA,
                            "시험 기록 대상 불일치")
            cutover_check()
        else:
            initial = baseline(directory, port)
            print("POST_CUTOVER_BASELINE=" + json.dumps(initial), flush=True)
            password = getpass.getpass("loadtest001 시험 계정 비밀번호(화면에 표시되지 않음): ")
            runtime.require(bool(password), "비밀번호 미입력; API 요청 없음")
            runtime.require(baseline(directory, port) == initial, "입력 대기 중 원장/재고 변경; 요청 중단")
            state = {"command_id": COMMAND, "target_schema": SCHEMA, "baseline": initial,
                     "request_id": str(uuid.uuid4()), "phase": "login_requested",
                     "login_requested_at_utc": canary.now()}
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w") as stream:
                json.dump(state, stream)
                stream.flush()
                os.fsync(stream.fileno())
            response = canary.post("/api/auth/login", {"email": canary.login.EMAIL, "password": password}, 200)
            token, _ = canary.login.login_data(response, time.time())
            state.update(phase="login_succeeded", login_completed_at_utc=canary.now())
            runtime.secure_write(path, state)
            print("POST_CUTOVER_LOGIN_HTTP=200 TEST_USER_ID_VERIFIED=524", flush=True)
            runtime.require(baseline(directory, port) == initial, "로그인 이후 원장/재고 변경; 선물 중단")
            state.update(phase="gift_requested", gift_requested_at_utc=canary.now())
            runtime.secure_write(path, state)
            started = time.monotonic_ns()
            response = canary.post("/api/gifts", {"productId":4, "expectedUnitPrice":22900,
                                   "recipientUserId":577, "quantity":1}, 201, token, state["request_id"])
            gift_id = response.get("data", {}).get("gift", {}).get("giftId")
            runtime.require(type(gift_id) is int and gift_id > initial["last_gift_id"], "선물 응답 ID 불일치; 재요청 금지")
            state.update(phase="gift_http_succeeded", gift_id=gift_id,
                         gift_completed_at_utc=canary.now(), gift_api_ms=(time.monotonic_ns()-started)/1e6)
            runtime.secure_write(path, state)
            print("POST_CUTOVER_GIFT_HTTP=201 GIFT_ID=" + str(gift_id), flush=True)
        with connect(directory, port) as connection, connection.cursor() as cursor:
            verified = verify(cursor, state)
        state.update(phase="target_write_verified", verification=verified)
        runtime.secure_write(path, state)
        print(json.dumps(verified, ensure_ascii=False, indent=2))
        print("POST_CUTOVER_REAL_GIFT_AND_STOCK_VERIFIED_TEST_COMPLETE")
        print("NO_PASSWORD_OR_TOKEN_OUTPUT_NO_SOURCE_WRITE_NO_RETRY_NO_SIGNUP")


if __name__ == "__main__":
    try:
        main()
    except (EOFError, KeyboardInterrupt):
        raise SystemExit("STOP: 입력 취소; 기록 존재 시 재실행하지 말고 --verify-only 사용")
    except Exception as error:
        detail = str(error) if isinstance(error, runtime.SafetyStop) else type(error).__name__
        raise SystemExit("STOP: " + detail + "; API 재요청·시험기록 삭제 금지")
