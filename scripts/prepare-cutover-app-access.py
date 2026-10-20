#!/usr/bin/env python3
"""Mac: 기본 권한 계획 조회. 이중 승인 시 새 Target DML4권한 GRANT만 수행."""
import argparse
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path

spec = importlib.util.spec_from_file_location("app_access", Path(__file__).with_name("inspect-cutover-app-access.py"))
access = importlib.util.module_from_spec(spec)
spec.loader.exec_module(access)
runtime = access.runtime
RECORD = "cutover-app-access-change.json"


def add_permissions(cursor, directory):
    record = directory / RECORD
    runtime.require(not record.exists() and not record.is_symlink(),
                    "기존 권한 변경 기록 존재: 상태 확인 전 재실행 금지")
    # 오래된 사용자 출력이 아닌 현재의 Host 계정·TLS·기존권한·Target 존재 재확인.
    plan = access.inspect(cursor)
    original = access.permissions.grant_map(plan["existing_grants"])
    target_scope = plan["new_scope"]
    runtime.require(target_scope not in original, "새Target 권한 이미 존재: 덮어쓰기 중단")
    state = {"phase": "grant_requested", "requested_at_utc": datetime.now(timezone.utc).isoformat(),
             "user": access.USER, "host": access.HOST, "target_scope": target_scope,
             "existing_grants": plan["existing_grants"],
             "added_privileges": ["SELECT", "INSERT", "UPDATE", "DELETE"]}
    # GRANT는 자동커밋. 동시 실행 차단 및 응답 유실 시 보존을 위해 SQL 전에 intent 기록.
    descriptor = os.open(record, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(state, stream)
        stream.flush()
        os.fsync(stream.fileno())
    print("APP_TARGET_SCOPED_GRANT_REQUESTED", flush=True)
    try:
        cursor.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON " + target_scope + " TO %s@%s",
                       (access.USER, access.HOST))
        state["phase"] = "grant_returned"
        runtime.secure_write(record, state)
        cursor.execute("SHOW GRANTS FOR %s@%s", (access.USER, access.HOST))
        actual = access.permissions.grant_map(access.permissions.access.summarize_grants(cursor.fetchall()))
        expected = dict(original)
        expected[target_scope] = access.PRIVILEGES
        runtime.require(actual == expected, "사후 권한 차이: 기존 권한/새Target 범위 조사 필요")
        cursor.execute("SELECT ssl_type FROM mysql.user WHERE User=%s AND Host=%s",
                       (access.USER, access.HOST))
        runtime.require(cursor.fetchone() == ("ANY",), "사후 Host 계정/TLS 요건 불일치")
        cursor.execute("SELECT @@GLOBAL.partial_revokes")
        runtime.require(access.permissions.scope(access.permissions.access.TARGET_SCHEMA,
                                                cursor.fetchone()[0]) == target_scope,
                        "권한 변경 중 전역 partial_revokes 상태 변화")
        state.update(phase="scoped_grant_verified", verified_at_utc=datetime.now(timezone.utc).isoformat())
        runtime.secure_write(record, state)
    except Exception:
        state["phase"] = "grant_result_or_verification_uncertain"
        runtime.secure_write(record, state)
        raise
    print("APP_NEW_TARGET_DML_GRANT_VERIFIED")
    print("EXISTING_GRANTS_PRESERVED_SSL_REQUIREMENT_VERIFIED")
    print("NO_PASSWORD_ACCOUNT_DDL_DATA_APP_OR_TRAFFIC_CHANGE")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True)
    parser.add_argument("--local-port", type=int, default=13306)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--approved-app-target-grant", action="store_true")
    args = parser.parse_args()
    runtime.require(not args.apply or args.approved_app_target_grant, "새 Target 앱 권한 추가 승인 필요")
    runtime.require(1 <= args.local_port <= 65535, "포트 범위 오류")
    directory = runtime.protected_directory(args.directory)
    master = access.permissions.access.helper.management_credentials()
    with runtime.connect_db(directory, args.local_port, master["username"], master["password"]) as connection:
        with connection.cursor() as cursor:
            if args.apply:
                add_permissions(cursor, directory)
            else:
                print(json.dumps(access.inspect(cursor), ensure_ascii=False, indent=2))
                print("APP_TARGET_ACCESS_PLAN_OK_NO_DB_WRITE")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        detail = str(error) if isinstance(error, runtime.SafetyStop) else type(error).__name__
        raise SystemExit("STOP: " + detail + "; 변경 기록 존재 시 부분 상태 확인·재실행 금지")
