#!/usr/bin/env python3
"""Mac: 기존 후보 계정의 새 Target 접근 권한 계획만 조회. GRANT/앱 기동 없음."""
import argparse
import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location("permissions", Path(__file__).with_name("prepare-cutover-dms-permissions.py"))
permissions = importlib.util.module_from_spec(spec)
spec.loader.exec_module(permissions)
runtime = permissions.runtime
USER = "seonjalal_staging_app"
HOST = "172.31.6.212"
PRIVILEGES = {"SELECT", "INSERT", "UPDATE", "DELETE"}


def inspect(cursor):
    cursor.execute("SELECT ssl_type FROM mysql.user WHERE User=%s AND Host=%s", (USER, HOST))
    runtime.require(cursor.fetchone() == ("ANY",), "기존 후보 Host 계정/TLS 요건 확인 필요")
    cursor.execute("SHOW GRANTS FOR %s@%s", (USER, HOST))
    grants = permissions.access.summarize_grants(cursor.fetchall())
    current = permissions.grant_map(grants)
    normalized = {key.replace("\\", ""): value for key, value in current.items()}
    runtime.require(normalized == {"*.*": {"USAGE"}, "`seonjalal_develop`.*": PRIVILEGES},
                    "기존 후보 계정 권한이 예상과 다름: 변경 계획 자동 진행 금지")
    cursor.execute("SELECT @@GLOBAL.partial_revokes")
    partial = cursor.fetchone()[0]
    runtime.require(partial in (0, 1), "partial_revokes 확인 필요")
    cursor.execute("SELECT COUNT(*) FROM information_schema.tables "
                   "WHERE table_schema=%s AND table_type='BASE TABLE'", (permissions.access.TARGET_SCHEMA,))
    runtime.require(cursor.fetchone() == (16,), "새 Target16테이블 확인 필요")
    return {"mode": "PLAN_ONLY", "instance": runtime.DB_ID, "user": USER, "host": HOST,
            "existing_grants": grants, "new_scope": permissions.scope(permissions.access.TARGET_SCHEMA, partial),
            "proposed_privileges": "SELECT, INSERT, UPDATE, DELETE", "new_accounts": 0,
            "password_tls_and_existing_grants_unchanged": True, "app_started": False,
            "tradeoff": "Reuse the existing nonproduction Host account; it can access both old and new schemas"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True)
    parser.add_argument("--local-port", type=int, default=13306)
    args = parser.parse_args()
    runtime.require(1 <= args.local_port <= 65535, "포트 범위 오류")
    directory = runtime.protected_directory(args.directory)
    master = permissions.access.helper.management_credentials()
    with runtime.connect_db(directory, args.local_port, master["username"], master["password"]) as connection:
        with connection.cursor() as cursor:
            result = inspect(cursor)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("APP_TARGET_ACCESS_PLAN_OK_NO_DB_WRITE")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        detail = str(error) if isinstance(error, runtime.SafetyStop) else type(error).__name__
        raise SystemExit("STOP: " + detail)
