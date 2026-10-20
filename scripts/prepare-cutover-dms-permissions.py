#!/usr/bin/env python3
"""Mac: 새 Target/제어 schema 한정 권한 계획. 승인 옵션에서만 DB/GRANT 추가."""
import argparse
import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "dms_access", Path(__file__).with_name("inspect-cutover-dms-access.py"))
access = importlib.util.module_from_spec(spec)
spec.loader.exec_module(access)
runtime = access.runtime
USER = "dms_rehearsal"
HOST = "%"
PRIVILEGES = {"SELECT", "INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "INDEX", "ALTER",
              "CREATE TEMPORARY TABLES"}
PRIVILEGE_SQL = "SELECT, INSERT, UPDATE, DELETE, CREATE, DROP, INDEX, ALTER, CREATE TEMPORARY TABLES"
CONTROL_CREATE = (f"CREATE DATABASE `{access.CONTROL_SCHEMA}` "
                  "CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci")


def scope(schema, partial_revokes):
    runtime.require(schema in (access.TARGET_SCHEMA, access.CONTROL_SCHEMA), "권한 대상 범위 오류")
    runtime.require(partial_revokes in (0, 1), "partial_revokes 상태 오류")
    # GRANT의 DB명은 백틱만으로 _ 와일드카드가 제거되지 않음. 전역 설정은 변경하지 않음.
    exact = schema if partial_revokes else schema.replace("_", "\\_")
    return "`" + exact + "`.*"


def grant_map(grants):
    result = {}
    for row in grants:
        runtime.require("scope" in row and not row.get("grant_option"), "예상 밖 역할/재부여 권한")
        key = row["scope"].replace("\\\\", "\\")
        runtime.require(key not in result, "중복 권한 범위 확인 필요")
        result[key] = set(row["privileges"])
    return result


def preflight(cursor):
    metadata = access.inspect_target(cursor, USER)
    runtime.require(metadata["local_infile"] == 1 and not metadata["control_schema_exists"],
                    "local_infile/새 제어 schema 존재 상태 확인 필요")
    runtime.require(len(metadata["accounts"]) == 1, "복제 계정 Host 구성 변경")
    account = metadata["accounts"][0]
    runtime.require(account["host"] == HOST and account["ssl_requirement"] == "ANY",
                    "기존 복제 계정 Host/TLS 요건 변경")
    current = grant_map(account["direct_grants"])
    expected = {"*.*": {"USAGE"}, "`awsdms_control`.*": {"ALL PRIVILEGES"},
                "`seonjalal_develop`.*": PRIVILEGES}
    runtime.require(current == expected, "복제 계정 기존 권한 변경: 자동 추가 중단")
    cursor.execute("SELECT @@GLOBAL.partial_revokes")
    partial = cursor.fetchone()[0]
    runtime.require(partial in (0, 1), "partial_revokes 상태 확인 실패")
    return current, int(partial)


def apply_permissions(cursor, record):
    runtime.require(not record.exists(), "이전 권한 추가 기록 존재: 재실행 금지")
    original, partial = preflight(cursor)
    state = {"user": USER, "host": HOST, "target_schema": access.TARGET_SCHEMA,
             "control_schema": access.CONTROL_SCHEMA, "phase": "requested", "completed_steps": []}
    runtime.secure_write(record, state)
    print("DMS_SCOPED_PERMISSION_CHANGE_REQUESTED", flush=True)
    try:
        # 기존 계정/비밀번호/TLS/권한을 변경하지 않고 새 schema와 권한만 추가.
        cursor.execute(CONTROL_CREATE)
        state["completed_steps"].append("control_schema_created")
        runtime.secure_write(record, state)
        cursor.execute("GRANT " + PRIVILEGE_SQL + " ON " + scope(access.TARGET_SCHEMA, partial)
                       + " TO %s@%s", (USER, HOST))
        state["completed_steps"].append("target_grant_added")
        runtime.secure_write(record, state)
        cursor.execute("GRANT ALL PRIVILEGES ON " + scope(access.CONTROL_SCHEMA, partial)
                       + " TO %s@%s", (USER, HOST))
        state["completed_steps"].append("control_grant_added")
        runtime.secure_write(record, state)
        cursor.execute("SHOW GRANTS FOR %s@%s", (USER, HOST))
        actual = grant_map(access.summarize_grants(cursor.fetchall()))
        expected = dict(original)
        expected[scope(access.TARGET_SCHEMA, partial)] = PRIVILEGES
        expected[scope(access.CONTROL_SCHEMA, partial)] = {"ALL PRIVILEGES"}
        runtime.require(actual == expected, "권한 추가 후 검증 불일치: 부분 상태 확인 필요")
        cursor.execute("SELECT Host,ssl_type FROM mysql.user WHERE User=%s", (USER,))
        runtime.require(cursor.fetchall() == ((HOST, "ANY"),), "계정 Host/TLS 사후 확인 실패")
        cursor.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=%s",
                       (access.CONTROL_SCHEMA,))
        runtime.require(cursor.fetchone() == (0,), "새 제어 schema 예상 밖 테이블")
        state["phase"] = "scoped_grants_verified"
        runtime.secure_write(record, state)
    except Exception:
        state["phase"] = "partial_or_verification_failure"
        runtime.secure_write(record, state)
        raise
    print("NEW_CONTROL_SCHEMA_CREATED_EMPTY")
    print("DMS_NEW_TARGET_AND_CONTROL_GRANTS_VERIFIED")
    print("EXISTING_GRANTS_TLS_AND_PASSWORD_UNCHANGED_NO_DMS_OR_APP_CHANGE")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True)
    parser.add_argument("--local-port", type=int, default=13306)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--approved-scoped-dms-grants", action="store_true")
    args = parser.parse_args()
    runtime.require(not args.apply or args.approved_scoped_dms_grants, "신규 schema/권한 추가 승인 옵션 필요")
    runtime.require(1 <= args.local_port <= 65535, "포트 범위 오류")
    directory = runtime.protected_directory(args.directory)
    master = access.helper.management_credentials()
    tasks = runtime.aws("dms", "describe-replication-tasks", "--without-settings", "--filters",
                        "Name=replication-task-id,Values=" + access.BASE_TASK)["ReplicationTasks"]
    runtime.require(len(tasks) == 1 and tasks[0]["Status"] == "stopped"
                    and tasks[0]["TargetEndpointArn"] == access.TARGET_ENDPOINT,
                    "기존 DMS Task 상태/대상 변경")
    with runtime.connect_db(directory, args.local_port, master["username"], master["password"]) as connection:
        with connection.cursor() as cursor:
            if args.apply:
                apply_permissions(cursor, directory / "cutover-dms-permissions.json")
            else:
                _, partial = preflight(cursor)
                print(json.dumps({"mode": "PLAN_ONLY", "instance": runtime.DB_ID,
                                  "create_empty_control_schema": access.CONTROL_SCHEMA,
                                  "user": USER, "host": HOST, "partial_revokes": partial,
                                  "new_grants": [
                                      {"scope": scope(access.TARGET_SCHEMA, partial),
                                       "privileges": PRIVILEGE_SQL},
                                      {"scope": scope(access.CONTROL_SCHEMA, partial),
                                       "privileges": "ALL PRIVILEGES"}],
                                  "existing_grants_removed": False,
                                  "password_tls_or_global_settings_changed": False,
                                  "dms_started": False}, ensure_ascii=False, indent=2))
                print("DMS_PERMISSION_PLAN_OK_NO_DB_WRITE")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        detail = str(error) if isinstance(error, runtime.SafetyStop) else type(error).__name__
        raise SystemExit("STOP: " + detail + "; 변경 표식 이후 실패 시 부분 상태 확인·재실행 금지")
