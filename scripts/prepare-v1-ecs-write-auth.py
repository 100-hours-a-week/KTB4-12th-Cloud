#!/usr/bin/env python3
"""시험 계정의 단기 JWT를 보호 파일로 준비하고 읽기 성격의 선물 preflight만 호출."""
import argparse
import base64
import hashlib
import hmac
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import time
import urllib.error
import urllib.request
import uuid

spec = importlib.util.spec_from_file_location("candidate_db", Path(__file__).with_name("prepare-v1-candidate-db.py"))
db = importlib.util.module_from_spec(spec)
spec.loader.exec_module(db)
runtime = db.runtime
BASE = "http://127.0.0.1:18084"


def read_plan(directory, path):
    path = Path(path)
    runtime.require(not path.is_symlink() and path.resolve().parent == directory, "계획 파일 경로 불일치")
    info = path.stat()
    runtime.require(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o600, "계획 파일 보호 권한 불일치")
    plan = json.loads(path.read_text())
    expected = {"account": runtime.ACCOUNT, "db": runtime.DB_ID, "schema": runtime.DB_NAME,
                "sender_id": 524, "recipient_id": 577, "product_id": 4, "quantity": 1,
                "expected_unit_price": 22900, "before_stock": 87, "before_gift_count": 5022,
                "before_latest_gift_id": 5022, "max_new_gifts": 30, "business_write_executed": False}
    runtime.require(all(plan.get(key) == value for key, value in expected.items()), "승인된 시험 기준과 불일치")
    keys = plan.get("request_keys", [])
    runtime.require(len(keys) == 30 and len(set(keys)) == 30 and all(str(uuid.UUID(key)) == key for key in keys),
                    "시험 키 30개 유일성/형식 불일치")
    return plan


def verify_baseline(directory, port, plan):
    master = db.management_credentials()
    with runtime.connect_db(directory, port, master["username"], master["password"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute("START TRANSACTION READ ONLY")
            try:
                cursor.execute(f"SELECT COUNT(*),MAX(id) FROM `{runtime.DB_NAME}`.gift_histories")
                runtime.require(cursor.fetchone() == (plan["before_gift_count"], plan["before_latest_gift_id"]),
                                "기준 선물 원장 변경 감지. 재시험/추가 쓰기 중단")
                cursor.execute(f"SELECT price,quantity,deleted_at FROM `{runtime.DB_NAME}`.products WHERE id=%s", (plan["product_id"],))
                product = cursor.fetchone()
                runtime.require(product and int(product[0]) == plan["expected_unit_price"] and
                                product[1] == plan["before_stock"] and product[2] is None, "상품 기준 변경 감지")
                cursor.execute(f"SELECT id FROM `{runtime.DB_NAME}`.users WHERE id IN (%s,%s) AND status='ACTIVE' AND deleted_at IS NULL ORDER BY id",
                               (plan["sender_id"], plan["recipient_id"]))
                runtime.require([row[0] for row in cursor.fetchall()] == sorted([plan["sender_id"], plan["recipient_id"]]),
                                "시험 사용자 상태 변경")
                cursor.execute(f"SELECT COUNT(*) FROM `{runtime.DB_NAME}`.friends WHERE user_id=%s AND friend_user_id=%s AND deleted_at IS NULL",
                               (plan["sender_id"], plan["recipient_id"]))
                runtime.require(cursor.fetchone()[0] == 1, "친구 관계 변경 감지")
            finally:
                cursor.execute("ROLLBACK")


def make_token(secret_base64, subject, now):
    key = base64.b64decode(secret_base64, validate=True)
    runtime.require(len(key) >= 32 and subject == 524, "시험 서명 키/발신자 확인 필요")
    encode = lambda value: base64.urlsafe_b64encode(value).rstrip(b"=").decode()
    header = encode(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    payload = encode(json.dumps({"iss": "seonjalal-staging", "sub": str(subject),
                                 "iat": now, "exp": now + 1800}, separators=(",", ":")).encode())
    signing_input = header + "." + payload
    return signing_input + "." + encode(hmac.new(key, signing_input.encode(), hashlib.sha256).digest())


def connection_failure(error):
    # URL/인증 헤더/토큰 원문은 표시하지 않고 원인 종류만 반환.
    reason = error.reason
    if isinstance(reason, ConnectionRefusedError):
        return "Mac 18084 연결 거부: 시험용 SSM 터널 리스너 확인 필요"
    if isinstance(reason, TimeoutError):
        return "시험 API 연결/응답 시간 초과: 터널과 원격 프록시 상태 확인 필요"
    return "시험 API 연결 실패: " + type(reason).__name__


def verify_tunnel():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(BASE + "/actuator/health", timeout=5) as response:
            runtime.require(response.status == 200 and response.headers.get("X-Rehearsal-Upstream") == "v1"
                            and json.loads(response.read())["status"] == "UP", "시험 진입점 v1 상태 불일치")
    except urllib.error.HTTPError as error:
        raise runtime.SafetyStop("시험 Health HTTP=" + str(error.code)) from None
    except urllib.error.URLError as error:
        raise runtime.SafetyStop(connection_failure(error)) from None


def auth_preflight(plan, token, expected_target="v1"):
    # 이 API는 선물 조건 조회만 수행. /gifts 생성 API는 호출하지 않음.
    request = urllib.request.Request(BASE + "/gifts/preflight", method="POST",
        data=json.dumps({"productId": plan["product_id"], "recipientUserId": plan["recipient_id"],
                         "quantity": 1}).encode(),
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json", "Accept": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=10) as response:
            runtime.require(response.status == 200 and response.headers.get("X-Rehearsal-Upstream") == expected_target,
                            "인증 사전 검증 대상/상태 불일치")
            body = json.loads(response.read())
    except urllib.error.HTTPError as error:
        raise runtime.SafetyStop("인증 사전 검증 HTTP=" + str(error.code)) from None
    except urllib.error.URLError as error:
        raise runtime.SafetyStop(connection_failure(error)) from None
    product = body.get("data", {}).get("product", {})
    recipient = body.get("data", {}).get("recipient", {})
    runtime.require(body.get("error") is None and recipient.get("userId") == plan["recipient_id"] and
                    product.get("productId") == plan["product_id"] and product.get("unitPrice") == plan["expected_unit_price"] and
                    product.get("quantity") == 1 and product.get("maxOrderQuantity", 0) >= 1,
                    "실제 선물 사전 검증 응답 계약 불일치")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True)
    parser.add_argument("--fixture-plan", required=True)
    parser.add_argument("--local-port", type=int, default=13306)
    parser.add_argument("--approved-fixture-auth", action="store_true")
    args = parser.parse_args()
    runtime.require(args.approved_fixture_auth, "시험 인증 준비 승인 옵션 필요")
    directory = runtime.protected_directory(args.directory)
    plan_path = Path(args.fixture_plan)
    plan = read_plan(directory, plan_path)
    # 터널이 없으면 AWS/DB 조회와 서명 준비 전에 빠르게 중단.
    verify_tunnel()
    print("LOCAL_TEST_TUNNEL_V1_HEALTH_OK", flush=True)
    verify_baseline(directory, args.local_port, plan)
    secret = runtime.aws("secretsmanager", "get-secret-value", "--secret-id", runtime.SECRET_ARN,
                         "--version-stage", "AWSCURRENT")
    runtime.require(secret["ARN"] == runtime.SECRET_ARN, "시험 Secret 대상 불일치")
    payload = json.loads(secret["SecretString"])
    now = int(time.time())
    token = make_token(payload["JWT_SECRET_BASE64"], plan["sender_id"], now)
    auth_preflight(plan, token)
    verify_baseline(directory, args.local_port, plan)
    target = directory / ("v1-ecs-write-auth-" + uuid.uuid4().hex + ".json")
    runtime.secure_write(target, {"access_token": token, "expires_at": now + 1800,
        "fixture_plan": str(plan_path.resolve()), "fixture_sha256": hashlib.sha256(plan_path.read_bytes()).hexdigest(),
        "secret_version_id": secret["VersionId"], "sender_id": plan["sender_id"],
        "auth_preflight_target": "v1", "business_write_executed": False})
    print("TEST_ACCOUNT_JWT_PREFLIGHT_HTTP=200")
    print("AUTH_PREFLIGHT_UPSTREAM=v1")
    print("BASELINE_GIFT_AND_STOCK_UNCHANGED")
    print("TEST_AUTH_EXPIRES_IN_SECONDS=1800")
    print("TEST_AUTH_FILE=" + str(target))
    print("NO_GIFT_CREATE_OR_SECRET_VALUE_OUTPUT")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        code = error.args[0] if error.args and isinstance(error.args[0], int) else type(error).__name__
        detail = str(error) if isinstance(error, runtime.SafetyStop) else "상세 원문 비공개"
        print(f"STOP CODE={code}: {detail}", file=sys.stderr)
        sys.exit(1)
