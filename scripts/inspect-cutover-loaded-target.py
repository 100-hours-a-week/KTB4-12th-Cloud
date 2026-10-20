#!/usr/bin/env python3
"""Mac: 새 Target 실제 적재 위치·행수 조회. DB 데이터/Secret/AWS 변경 없음."""
import argparse
import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "access", Path(__file__).with_name("inspect-cutover-dms-access.py"))
access = importlib.util.module_from_spec(spec)
spec.loader.exec_module(access)
runtime = access.runtime


def inspect(cursor):
    # 업무 행의 이메일·토큰 등은 읽어 출력하지 않고 집계값만 확인.
    cursor.execute("START TRANSACTION READ ONLY")
    try:
        schema = access.TARGET_SCHEMA
        cursor.execute("SELECT table_name FROM information_schema.tables "
                       "WHERE table_schema=%s AND table_type='BASE TABLE'", (schema,))
        runtime.require(sorted(row[0] for row in cursor.fetchall()) == access.TABLES,
                        "새 Target의 정확한16테이블 목록 불일치")
        counts = {}
        for name in access.TABLES:
            # schema/table은 사용자 입력이 아닌 코드 고정 allowlist.
            cursor.execute(f"SELECT COUNT(*) FROM `{schema}`.`{name}`")
            counts[name] = int(cursor.fetchone()[0])
        runtime.require(counts["users"] > 0 and counts["products"] > 0
                        and counts["gift_histories"] > 0, "새 Target 핵심 테이블 적재 미확인")
        cursor.execute("SELECT COUNT(*) FROM information_schema.referential_constraints "
                       "WHERE constraint_schema=%s", (schema,))
        fk_count = int(cursor.fetchone()[0])
        runtime.require(fk_count == 18, "Target 외래키18개 메타데이터 불일치")
        cursor.execute(f"SELECT COUNT(*),COUNT(DISTINCT idempotency_key),MAX(id) "
                       f"FROM `{schema}`.gift_histories")
        gifts, keys, last_id = cursor.fetchone()
        cursor.execute(f"SELECT id,quantity FROM `{schema}`.products WHERE id IN (3,4) ORDER BY id")
        stock = [{"id": int(row[0]), "quantity": int(row[1])} for row in cursor.fetchall()]
        # 기존 RDS schema는 보존 확인을 위한 읽기만 수행. 건수만으로 불변 증명 불가.
        cursor.execute("SELECT COUNT(*) FROM `seonjalal_develop`.gift_histories")
        existing_gifts = int(cursor.fetchone()[0])
        return {"target_schema": schema, "target_table_counts": counts,
                "target_total_rows": sum(counts.values()), "foreign_key_definitions": fk_count,
                "gift_rows": int(gifts), "distinct_nonnull_gift_keys": int(keys),
                "latest_gift_id": last_id, "target_stock": stock,
                "existing_rds_schema_gift_count": existing_gifts,
                "limitation": "Target snapshot only; no Source equality, FK orphan or CDC canary proof"}
    finally:
        cursor.execute("ROLLBACK")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True)
    parser.add_argument("--local-port", type=int, default=13306)
    args = parser.parse_args()
    runtime.require(1 <= args.local_port <= 65535, "포트 범위 오류")
    directory = runtime.protected_directory(args.directory)
    master = access.helper.management_credentials()
    with runtime.connect_db(directory, args.local_port, master["username"], master["password"]) as connection:
        with connection.cursor() as cursor:
            result = inspect(cursor)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("TARGET_LOAD_LOCATION_READ_VERIFIED_NO_DB_WRITE")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        detail = str(error) if isinstance(error, runtime.SafetyStop) else type(error).__name__
        raise SystemExit("STOP: " + detail)
