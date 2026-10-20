#!/usr/bin/env python3
"""Mac: 기존 TLS 터널로 시험 DB 분리 가능성만 조회. DB/AWS 쓰기 없음."""
import argparse
import importlib.util
import json
from pathlib import Path

SCHEMA = "seonjalal_cutover_20261019"
BASE_SCHEMA = "seonjalal_develop"
spec = importlib.util.spec_from_file_location(
    "candidate_db", Path(__file__).with_name("prepare-v1-candidate-db.py"))
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


def inspect(cursor):
    """과거 원장 기대값을 강제하지 않고 현재 메타데이터만 수집."""
    cursor.execute("START TRANSACTION READ ONLY")
    try:
        cursor.execute("SELECT schema_name, default_character_set_name, default_collation_name "
                       "FROM information_schema.schemata WHERE schema_name IN (%s,%s) ORDER BY schema_name",
                       (BASE_SCHEMA, SCHEMA))
        schemas = cursor.fetchall()
        helper.runtime.require(any(row[0] == BASE_SCHEMA for row in schemas),
                               "기존 시험 schema 미확인")
        helper.runtime.require(not any(row[0] == SCHEMA for row in schemas),
                               "새 시험 schema 이미 존재: 덮어쓰기 없이 중단")
        cursor.execute("SELECT table_type,COUNT(*),COALESCE(SUM(data_length+index_length),0) "
                       "FROM information_schema.tables WHERE table_schema=%s GROUP BY table_type",
                       (BASE_SCHEMA,))
        tables = [{"type": row[0], "count": int(row[1]), "estimated_bytes": int(row[2])}
                  for row in cursor.fetchall()]
        cursor.execute("SELECT COUNT(*) FROM information_schema.triggers WHERE trigger_schema=%s",
                       (BASE_SCHEMA,))
        triggers = int(cursor.fetchone()[0])
        cursor.execute("SELECT COUNT(*) FROM information_schema.key_column_usage "
                       "WHERE table_schema=%s AND referenced_table_schema IS NOT NULL "
                       "AND referenced_table_schema<>%s", (BASE_SCHEMA, BASE_SCHEMA))
        external_fk = int(cursor.fetchone()[0])
        helper.runtime.require(external_fk == 0,
                               "다른 schema 참조 FK 감지: 격리 설계 검토 필요")
        # 수치는 메타데이터 추정치. 실제 Dump 크기·복제 시간으로 취급하지 않음.
        return {"existing_schema": BASE_SCHEMA, "new_schema": SCHEMA,
                "new_schema_absent": True,
                "charset": schemas[0][1], "collation": schemas[0][2],
                "tables": tables, "triggers": triggers,
                "cross_schema_fk_columns": external_fk,
                "limitation": "Metadata only; no fixture privacy, capacity or create privilege guarantee"}
    finally:
        cursor.execute("ROLLBACK")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True)
    parser.add_argument("--local-port", type=int, default=13306)
    args = parser.parse_args()
    helper.runtime.require(1 <= args.local_port <= 65535, "포트 범위 오류")
    directory = helper.runtime.protected_directory(args.directory)
    # 기존 도구가 계정·RDS 식별자·endpoint를 확인한 뒤 관리 Secret을 메모리에서만 사용.
    master = helper.management_credentials()
    with helper.runtime.connect_db(directory, args.local_port,
                                   master["username"], master["password"]) as connection:
        with connection.cursor() as cursor:
            result = inspect(cursor)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("ISOLATION_METADATA_READ_OK_NO_DB_OR_SECRET_WRITE")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # 드라이버 예외에는 SQL/인증 정보가 포함될 수 있으므로 원문 출력 금지.
        detail = str(error) if isinstance(error, helper.runtime.SafetyStop) else type(error).__name__
        raise SystemExit("STOP: " + detail)
