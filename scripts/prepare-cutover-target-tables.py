#!/usr/bin/env python3
"""Mac: 검토된 Source DDL의 CREATE TABLE만 새 시험 schema에 적용. 기본은 계획 조회."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat

spec = importlib.util.spec_from_file_location(
    "schema_source", Path(__file__).with_name("collect-cutover-source-schema.py"))
source = importlib.util.module_from_spec(spec)
spec.loader.exec_module(source)
spec_db = importlib.util.spec_from_file_location(
    "candidate_db", Path(__file__).with_name("prepare-v1-candidate-db.py"))
helper = importlib.util.module_from_spec(spec_db)
spec_db.loader.exec_module(helper)
TARGET = "seonjalal_cutover_20261019"
SOURCE_HASH = "4d865a79172751c5948dcd57f3b1469c8823d7644ef07c2dd26d993a4cc1daa9"


def parse_ddl(raw):
    # 일반 SQL 실행기가 아니라 직접 검토한 단일 Artifact만 승인.
    helper.runtime.require(hashlib.sha256(raw).hexdigest() == SOURCE_HASH,
                           "검토된 구조 해시 불일치")
    text = raw.decode("utf-8")
    source.validate_schema(text)
    text = re.sub(r"^/\*!\d{5} SET [^\n]+ \*/;\s*$", "", text, flags=re.M)
    pattern = r"CREATE TABLE `([a-z0-9_]+)` \([\s\S]*?\) ENGINE=InnoDB[^;\n]*;"
    matches = list(re.finditer(pattern, text))
    helper.runtime.require(sorted(match[1] for match in matches) == source.TABLES,
                           "CREATE TABLE 목록 불일치")
    helper.runtime.require(not re.sub(pattern, "", text).strip(), "허용하지 않은 추가 SQL 구문")
    return [(match[1], match[0]) for match in matches]


def read_artifact(value):
    path = Path(value)
    helper.runtime.require(not path.is_symlink() and not path.parent.is_symlink(),
                           "구조 파일 심볼릭 링크 금지")
    helper.runtime.require(path.parent.parent == Path("/private/tmp")
                           and path.parent.name.startswith("seonjalal-cutover-source.")
                           and path.name == "source-schema.sql", "구조 파일 경로 불일치")
    info = path.stat()
    helper.runtime.require(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o600,
                           "구조 파일 권한/소유자 불일치")
    return parse_ddl(path.read_bytes())


def check_empty(cursor):
    cursor.execute("SELECT default_character_set_name,default_collation_name "
                   "FROM information_schema.schemata WHERE schema_name=%s", (TARGET,))
    helper.runtime.require(cursor.fetchone() == ("utf8mb4", "utf8mb4_0900_ai_ci"),
                           "시험 schema 존재/문자셋 확인 필요")
    cursor.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=%s", (TARGET,))
    helper.runtime.require(cursor.fetchone() == (0,), "시험 DB가 비어있지 않음: 자동 덮어쓰기 금지")


def apply_tables(connection, cursor, statements, record):
    check_empty(cursor)
    helper.runtime.require(not record.exists(), "이전 적용 기록 존재: 재실행 대신 상태 확인")
    state = {"schema": TARGET, "source_sha256": SOURCE_HASH, "phase": "create_requested", "created": []}
    helper.runtime.secure_write(record, state)
    connection.select_db(TARGET)
    cursor.execute("SELECT DATABASE(),@@SESSION.foreign_key_checks")
    database, old_fk = cursor.fetchone()
    helper.runtime.require(database == TARGET and old_fk in (0, 1), "적용 세션 대상 확인 실패")
    print("TARGET_TABLE_CREATE_REQUESTED", flush=True)
    try:
        # 기존 DB의 전역 설정이 아닌 이번 연결에서만 FK 순서 제약 해제.
        cursor.execute("SET SESSION foreign_key_checks=0")
        for name, sql in statements:
            cursor.execute(sql)
            state["created"].append(name)
            helper.runtime.secure_write(record, state)
        cursor.execute("SELECT table_name FROM information_schema.tables WHERE table_schema=%s", (TARGET,))
        helper.runtime.require(sorted(row[0] for row in cursor.fetchall()) == source.TABLES,
                               "생성 후 테이블 목록 불일치")
        for name in source.TABLES:
            cursor.execute(f"SELECT COUNT(*) FROM `{TARGET}`.`{name}`")
            helper.runtime.require(cursor.fetchone() == (0,), "예상 밖 행 데이터 발견")
        cursor.execute("SELECT COUNT(*) FROM information_schema.referential_constraints "
                       "WHERE constraint_schema=%s", (TARGET,))
        expected_fk = sum(len(re.findall(r"CONSTRAINT `[^`]+` FOREIGN KEY", sql)) for _, sql in statements)
        helper.runtime.require(cursor.fetchone() == (expected_fk,), "외래키 생성 개수 불일치")
        state["phase"] = "created_empty_verified"
        helper.runtime.secure_write(record, state)
    except Exception:
        # CREATE는 자동 커밋. 생성된 빈 테이블/기록을 보존하고 임의 DROP하지 않음.
        state["phase"] = "partial_or_verification_failure"
        helper.runtime.secure_write(record, state)
        raise
    finally:
        cursor.execute("SET SESSION foreign_key_checks=" + str(old_fk))
    print("TARGET_TABLES_CREATED=16 TARGET_TOTAL_ROWS=0")
    print("TARGET_FOREIGN_KEYS_VERIFIED=" + str(expected_fk))
    print("NO_SOURCE_EXISTING_SCHEMA_ACCOUNT_APP_OR_DMS_CHANGE")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True)
    parser.add_argument("--schema-file", required=True)
    parser.add_argument("--local-port", type=int, default=13306)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--approved-target-tables-create", action="store_true")
    args = parser.parse_args()
    helper.runtime.require(not args.apply or args.approved_target_tables_create,
                           "시험 테이블 생성 승인 옵션 필요")
    helper.runtime.require(1 <= args.local_port <= 65535, "포트 범위 오류")
    statements = read_artifact(args.schema_file)
    directory = helper.runtime.protected_directory(args.directory)
    master = helper.management_credentials()
    with helper.runtime.connect_db(directory, args.local_port,
                                   master["username"], master["password"]) as connection:
        with connection.cursor() as cursor:
            if args.apply:
                apply_tables(connection, cursor, statements, directory / "cutover-target-tables.json")
            else:
                check_empty(cursor)
                print(json.dumps({"mode": "PLAN_ONLY", "target_instance": helper.runtime.DB_ID,
                                  "target_schema": TARGET, "create_tables": len(statements),
                                  "insert_rows": 0, "source_sha256": SOURCE_HASH,
                                  "foreign_keys": sum(len(re.findall(r"CONSTRAINT `[^`]+` FOREIGN KEY", sql))
                                                      for _, sql in statements)}, ensure_ascii=False, indent=2))
                print("TARGET_TABLE_PLAN_OK_NO_DB_WRITE")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        detail = str(error) if isinstance(error, helper.runtime.SafetyStop) else type(error).__name__
        raise SystemExit("STOP: " + detail + "; 생성 표식 이후 실패면 부분 상태 보존·재실행 금지")
