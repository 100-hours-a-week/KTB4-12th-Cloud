#!/usr/bin/env python3
"""Mac: 기록된 선물1건의 RDS CDC 반영 확인. 읽기 전용, API 재요청 없음."""
import argparse
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import stat
import uuid

spec = importlib.util.spec_from_file_location("canary", Path(__file__).with_name("run-cutover-cdc-canary.py"))
canary = importlib.util.module_from_spec(spec)
spec.loader.exec_module(canary)
runtime = canary.runtime
SCHEMA = canary.loaded.access.TARGET_SCHEMA


def read_record(directory):
    path = directory / canary.RECORD
    info = path.lstat()
    runtime.require(stat.S_ISREG(info.st_mode) and not path.is_symlink()
                    and info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o600,
                    "시험 기록 파일 소유자/권한/형식 불일치")
    record = json.loads(path.read_text())
    runtime.require(record.get("task_arn") == canary.TASK_ARN
                    and record.get("target_schema") == SCHEMA
                    and record.get("phase") == "gift_http_succeeded"
                    and (record.get("sender_id"), record.get("recipient_id"), record.get("product_id"),
                         record.get("quantity"), record.get("unit_price"), record.get("before_gifts"),
                         record.get("before_stock")) == (524, 577, 4, 1, 22900, 5096, 13)
                    and type(record.get("gift_id")) is int and record["gift_id"] > 5096,
                    "승인된 성공 canary 기록 불일치: 쓰기 재실행 금지")
    runtime.require(str(uuid.UUID(record["request_id"])) == record["request_id"], "멱등키 형식 오류")
    return record


def inspect(cursor, record):
    key = uuid.UUID(record["request_id"]).bytes
    cursor.execute("START TRANSACTION READ ONLY")
    try:
        cursor.execute(f"SELECT id,sender_id,recipient_id,product_id,quantity,product_price_snapshot,"
                       f"status,HEX(idempotency_key),deleted_at IS NULL FROM `{SCHEMA}`.gift_histories "
                       "WHERE id=%s AND sender_id=%s AND idempotency_key=%s",
                       (record["gift_id"], 524, key))
        rows = cursor.fetchall()
        runtime.require(len(rows) == 1, "Target 동일 선물·멱등키 행 미확인: 복제 상태 조회·쓰기 재실행 금지")
        row = rows[0]
        runtime.require(tuple(row[:5]) == (record["gift_id"], 524, 577, 4, 1)
                        and int(row[5]) == 22900 and row[6] == "COMPLETED"
                        and row[7].upper() == key.hex().upper() and row[8] == 1,
                        "Target 시험 선물 필드 불일치")
        cursor.execute(f"SELECT COUNT(*) FROM `{SCHEMA}`.gift_histories "
                       "WHERE sender_id=%s AND idempotency_key=%s", (524, key))
        runtime.require(cursor.fetchone() == (1,), "Target 시험 멱등키 중복/누락")
        cursor.execute(f"SELECT price,quantity FROM `{SCHEMA}`.products WHERE id=4 AND deleted_at IS NULL")
        product = cursor.fetchone()
        runtime.require(product is not None and int(product[0]) == 22900 and product[1] == 12,
                        "Target 상품4 가격/재고 불일치: 동시 쓰기·복제 상태 확인 필요")
        cursor.execute(f"SELECT COUNT(*),COUNT(DISTINCT idempotency_key),MAX(id) FROM `{SCHEMA}`.gift_histories")
        count, keys, latest = cursor.fetchone()
        runtime.require((int(count), int(keys), latest) == (5097, 5097, record["gift_id"]),
                        "Target 선물 전체 기준 변화: 추가 Writer/복제 상태 확인 필요")
        cursor.execute("SELECT COUNT(*) FROM `seonjalal_develop`.gift_histories")
        old_count = int(cursor.fetchone()[0])
        return {"target_schema": SCHEMA, "gift_id": record["gift_id"],
                "same_idempotency_key_verified": True, "gift_status": "COMPLETED",
                "gift_rows": int(count), "distinct_nonnull_gift_keys": int(keys),
                "product_4_stock": 12, "existing_rds_schema_gifts": old_count,
                "verified_at_utc": datetime.now(timezone.utc).isoformat(),
                "limitation": "One gift INSERT and stock UPDATE verified; no independent Source snapshot, "
                              "login session equality, DELETE, CDC latency or cutover availability proof"}
    finally:
        cursor.execute("ROLLBACK")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True)
    parser.add_argument("--local-port", type=int, default=13306)
    args = parser.parse_args()
    runtime.require(1 <= args.local_port <= 65535, "포트 범위 오류")
    directory = runtime.protected_directory(args.directory)
    record = read_record(directory)
    canary.check_task()
    master = canary.loaded.access.helper.management_credentials()
    with runtime.connect_db(directory, args.local_port, master["username"], master["password"]) as connection:
        with connection.cursor() as cursor:
            result = inspect(cursor, record)
    runtime.secure_write(directory / "cutover-cdc-canary-verification.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("TARGET_CDC_CANARY_GIFT_AND_STOCK_VERIFIED_NO_DB_WRITE")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        detail = str(error) if isinstance(error, runtime.SafetyStop) else type(error).__name__
        raise SystemExit("STOP: " + detail + "; API 재요청·기록/데이터 삭제 금지")
