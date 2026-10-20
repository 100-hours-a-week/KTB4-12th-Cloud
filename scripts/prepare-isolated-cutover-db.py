#!/usr/bin/env python3
"""Mac: 리허설 RDS 빈 시험 DB 생성 계획. 이중 승인 옵션에서만 CREATE 실행."""
import argparse
import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "isolation_inspect", Path(__file__).with_name("inspect-isolated-cutover-db.py"))
inspector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inspector)
helper = inspector.helper
CREATE_SQL = (f"CREATE DATABASE `{inspector.SCHEMA}` "
              "CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci")


def create_empty(cursor, metadata):
    # 기존 DB는 변경하지 않고, 이름 충돌은 MySQL 자체에서도 거절. IF NOT EXISTS 금지.
    helper.runtime.require(metadata.get("new_schema_absent") is True
                           and metadata.get("new_schema") == inspector.SCHEMA,
                           "새 시험 DB가 존재하지 않는지 확인 필요")
    helper.runtime.require(metadata.get("charset") == "utf8mb4"
                           and metadata.get("collation") == "utf8mb4_0900_ai_ci",
                           "기준 문자셋 불일치")
    print("EMPTY_SCHEMA_CREATE_REQUESTED", flush=True)
    cursor.execute(CREATE_SQL)
    # DDL은 자동 커밋. 사후 검사 실패여도 DROP/자동 재실행하지 않음.
    cursor.execute("SELECT default_character_set_name,default_collation_name "
                   "FROM information_schema.schemata WHERE schema_name=%s", (inspector.SCHEMA,))
    helper.runtime.require(cursor.fetchone() == ("utf8mb4", "utf8mb4_0900_ai_ci"),
                           "생성 후 문자셋 검사 실패: 부분 상태 보존")
    cursor.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=%s",
                   (inspector.SCHEMA,))
    helper.runtime.require(cursor.fetchone() == (0,), "새 DB에 예상 밖 테이블: 조사 필요")
    print("RDS_EMPTY_ISOLATED_SCHEMA_CREATED_TABLES=0")
    print("NO_EXISTING_SCHEMA_DATA_ACCOUNT_SECRET_OR_APP_CHANGE")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True)
    parser.add_argument("--local-port", type=int, default=13306)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--approved-empty-schema-create", action="store_true")
    args = parser.parse_args()
    helper.runtime.require(not args.apply or args.approved_empty_schema_create,
                           "빈 DB 생성의 명시적 승인 옵션 필요")
    helper.runtime.require(1 <= args.local_port <= 65535, "포트 범위 오류")
    directory = helper.runtime.protected_directory(args.directory)
    master = helper.management_credentials()
    with helper.runtime.connect_db(directory, args.local_port,
                                   master["username"], master["password"]) as connection:
        with connection.cursor() as cursor:
            metadata = inspector.inspect(cursor)
            helper.runtime.require(metadata["charset"] == "utf8mb4"
                                   and metadata["collation"] == "utf8mb4_0900_ai_ci",
                                   "문자셋 기준 불일치")
            if args.apply:
                create_empty(cursor, metadata)
            else:
                print(json.dumps({"mode": "PLAN_ONLY", "database_instance": helper.runtime.DB_ID,
                                  "schema": inspector.SCHEMA, "sql": CREATE_SQL,
                                  "existing_schema_modified": False,
                                  "new_tables_accounts_or_tasks": 0}, ensure_ascii=False, indent=2))
                print("PLAN_ONLY_NO_DB_OR_SECRET_WRITE")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        detail = str(error) if isinstance(error, helper.runtime.SafetyStop) else type(error).__name__
        raise SystemExit("STOP: " + detail + "; 생성 요청 이후 실패면 부분 상태 확인, 자동 삭제/재실행 금지")
