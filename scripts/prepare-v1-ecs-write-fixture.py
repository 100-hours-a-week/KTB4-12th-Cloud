#!/usr/bin/env python3
"""쓰기 시험 전 DB 구조/전용 Fixture 조회 및 로컬 기준 기록. DB 쓰기 없음."""
import argparse
from datetime import datetime, timezone
import importlib.util
import json
import re
from pathlib import Path
import sys
import uuid

spec = importlib.util.spec_from_file_location("candidate_db", Path(__file__).with_name("prepare-v1-candidate-db.py"))
candidate_db = importlib.util.module_from_spec(spec)
spec.loader.exec_module(candidate_db)
runtime = candidate_db.runtime
MAX_NEW_GIFTS = 30


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True)
    parser.add_argument("--local-port", type=int, default=13306)
    args = parser.parse_args()
    directory = runtime.protected_directory(args.directory)
    runtime.require(1 <= args.local_port <= 65535, "포트 범위 오류")
    master = candidate_db.management_credentials()
    with runtime.connect_db(directory, args.local_port, master["username"], master["password"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute("START TRANSACTION READ ONLY")
            try:
                cursor.execute(f"SELECT version, success FROM `{runtime.DB_NAME}`.flyway_schema_history ORDER BY installed_rank")
                runtime.require([tuple(row) for row in cursor.fetchall()] == [(str(n), 1) for n in range(1, 13)],
                                "고정 1.3.0의 스키마 이력과 불일치")
                cursor.execute("SELECT table_name FROM information_schema.tables WHERE table_schema=%s", (runtime.DB_NAME,))
                tables = {row[0] for row in cursor.fetchall()}
                runtime.require(not tables.intersection({"orders", "payment_cancellations", "credit_accounts", "credit_transactions"}),
                                "신규 결제/크레딧 구조 감지. 기존 선물 시험 계약 재검토 필요")
                cursor.execute(f"SELECT id,email,status,deleted_at FROM `{runtime.DB_NAME}`.users WHERE email=%s",
                               ("loadtest001@seonjalal.invalid",))
                users = cursor.fetchall()
                runtime.require(len(users) == 1 and users[0][1] == "loadtest001@seonjalal.invalid"
                                and users[0][2] == "ACTIVE" and users[0][3] is None,
                                "전용 시험 발신자 활성 상태 확인 필요")
                sender = users[0][0]
                # 이메일 순번이 인접해도 친구라는 보장은 없음. 기존 관계에서만 수신자 선택.
                cursor.execute(f"SELECT u.id,u.email,u.status,u.deleted_at FROM `{runtime.DB_NAME}`.friends f JOIN `{runtime.DB_NAME}`.users u ON u.id=f.friend_user_id WHERE f.user_id=%s AND f.deleted_at IS NULL AND u.status='ACTIVE' AND u.deleted_at IS NULL AND u.email REGEXP %s AND u.id<>%s ORDER BY u.id LIMIT 1",
                               (sender, r"^loadtest[0-9]{3}@seonjalal[.]invalid$", sender))
                recipients = cursor.fetchall()
                runtime.require(len(recipients) == 1 and recipients[0][0] != sender
                                and re.fullmatch(r"loadtest[0-9]{3}@seonjalal\.invalid", recipients[0][1])
                                and recipients[0][2] == "ACTIVE" and recipients[0][3] is None,
                                "기존 친구 중 활성 전용 시험 수신자 없음. 데이터 추가 없이 중단")
                recipient = recipients[0][0]
                cursor.execute(f"SELECT price,quantity,deleted_at FROM `{runtime.DB_NAME}`.products WHERE id=4")
                product = cursor.fetchone()
                runtime.require(product and product[2] is None and product[0] == int(product[0]) and product[0] >= 0,
                                "시험 상품 상태/가격 불일치")
                price, stock = int(product[0]), product[1]
                runtime.require(stock == 87 and stock >= MAX_NEW_GIFTS, "시험 상품 재고 기준 불일치")
                cursor.execute(f"SELECT COUNT(*),MAX(id) FROM `{runtime.DB_NAME}`.gift_histories")
                count, latest_id = cursor.fetchone()
                runtime.require((count, latest_id) == (5022, 5022), "시험 선물 원장 기준 불일치")
                cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_schema=%s AND table_name='gift_histories'",
                               (runtime.DB_NAME,))
                columns = {row[0] for row in cursor.fetchall()}
                runtime.require({"sender_id", "recipient_id", "product_id", "quantity", "product_price_snapshot",
                                 "status", "idempotency_key", "request_fingerprint"} <= columns,
                                "선물 원장 컬럼 계약 불일치")
                cursor.execute("SELECT column_name,non_unique FROM information_schema.statistics WHERE table_schema=%s AND table_name='gift_histories' AND index_name='uk_gift_histories_sender_idempotency' ORDER BY seq_in_index",
                               (runtime.DB_NAME,))
                runtime.require([tuple(row) for row in cursor.fetchall()] == [("sender_id", 0), ("idempotency_key", 0)],
                                "발신자/멱등키 유일성 제약 확인 필요")
            finally:
                cursor.execute("ROLLBACK")
    # 사용자 개인정보·서명키·로그인 토큰 없이 시험 기준과 계획 키만 로컬 기록.
    record = {
        "prepared_utc": datetime.now(timezone.utc).isoformat(), "account": runtime.ACCOUNT,
        "db": runtime.DB_ID, "schema": runtime.DB_NAME,
        "sender_id": sender, "recipient_id": recipient, "product_id": 4, "quantity": 1,
        "expected_unit_price": price, "before_stock": stock,
        "before_gift_count": count, "before_latest_gift_id": latest_id,
        "max_new_gifts": MAX_NEW_GIFTS,
        "request_keys": [str(uuid.uuid4()) for _ in range(MAX_NEW_GIFTS)],
        "execution_approved": False, "business_write_executed": False,
    }
    filename = "v1-ecs-write-fixture-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json"
    target = directory / filename
    runtime.secure_write(target, record)
    print("RDS_TLS_AND_FLYWAY_12_VERIFIED")
    print("TEST_USERS_ACTIVE_AND_FRIENDS")
    print("GIFT_IDEMPOTENCY_UNIQUE_CONSTRAINT_VERIFIED")
    print("SENDER_ID=" + str(sender))
    print("RECIPIENT_ID=" + str(recipient))
    print("PRODUCT_ID=4")
    print("EXPECTED_UNIT_PRICE=" + str(price))
    print("BEFORE_STOCK=" + str(stock))
    print("BEFORE_GIFT_COUNT=" + str(count))
    print("MAX_PROPOSED_NEW_GIFTS=" + str(MAX_NEW_GIFTS))
    print("WRITE_FIXTURE_PLAN=" + str(target))
    print("READONLY_PREFLIGHT_OK_NO_DB_OR_SECRET_WRITE")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        code = error.args[0] if error.args and isinstance(error.args[0], int) else type(error).__name__
        detail = str(error) if isinstance(error, runtime.SafetyStop) else "상세 원문 비공개"
        print(f"STOP CODE={code}: {detail}", file=sys.stderr)
        sys.exit(1)
