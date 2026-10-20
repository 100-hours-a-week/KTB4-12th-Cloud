#!/usr/bin/env python3
"""Mac: DMS 연결 메타데이터와 Target 권한만 읽기. 비밀번호/원문 연결속성 출력 금지."""
import argparse
import importlib.util
import json
from pathlib import Path
import re

spec = importlib.util.spec_from_file_location(
    "candidate_db", Path(__file__).with_name("prepare-v1-candidate-db.py"))
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)
runtime = helper.runtime
BASE_TASK = "seonjalal-db-readonly-cutover-20261015"
SOURCE_ENDPOINT = "arn:aws:dms:ap-northeast-2:721744297924:endpoint:FU25AATK25F45IHKJXYEN67M6U"
TARGET_ENDPOINT = "arn:aws:dms:ap-northeast-2:721744297924:endpoint:DFPTJDIP5VCNRLONAJLELMGEB4"
INSTANCE = "arn:aws:dms:ap-northeast-2:721744297924:rep:RHRA7UPZEBCZLEYTA6WIJUTU2Q"
TARGET_SCHEMA = "seonjalal_cutover_20261019"
CONTROL_SCHEMA = "seonjalal_cutover_control_20261019"
TABLES = sorted("categories flyway_schema_history friends gift_histories "
                "login_email_failure_limits login_ip_rate_limits product_images "
                "product_views products recipient_profiles recipient_recommended_products "
                "term_consents terms user_dislike_categories user_sessions users".split())


def endpoint(arn, kind):
    rows = runtime.aws("dms", "describe-endpoints", "--filters", "Name=endpoint-arn,Values=" + arn)["Endpoints"]
    runtime.require(len(rows) == 1 and rows[0]["EndpointArn"] == arn, "DMS Endpoint 불일치")
    result = rows[0]
    runtime.require(result["EndpointType"].upper() == kind and result["EngineName"] == "mysql",
                    "DMS Endpoint 유형/엔진 불일치")
    return result


def endpoint_summary(item):
    mysql = item.get("MySQLSettings") or {}
    extra = item.get("ExtraConnectionAttributes") or ""
    # 임의 연결 문자열/AfterConnectScript는 비밀번호나 SQL을 포함할 수 있어 원문 비출력.
    expected = bool(re.fullmatch(r"initstmt\s*=\s*SET\s+(?:SESSION\s+)?FOREIGN_KEY_CHECKS\s*=\s*0\s*;?",
                                 extra.strip(), re.I))
    return {"name": item["EndpointIdentifier"], "type": item["EndpointType"],
            "ssl_mode": item.get("SslMode"),
            "secret_backed": bool(mysql.get("SecretsManagerSecretId")),
            "database_name": item.get("DatabaseName") or mysql.get("DatabaseName"),
            "fk_disable_initstmt": expected,
            "other_extra_attributes_present": bool(extra) and not expected,
            "after_connect_script_present": bool(mysql.get("AfterConnectScript")),
            "target_db_type": mysql.get("TargetDbType")}


def summarize_grants(rows):
    result = []
    for row in rows:
        # GRANT의 TO 이후나 인증정보는 출력하지 않고 대상과 권한만 추출.
        match = re.match(r"^GRANT (.+?) ON (.+?) TO ", row[0])
        if match:
            result.append({"scope": match[2], "privileges": match[1].split(", "),
                           "grant_option": " WITH GRANT OPTION" in row[0]})
        else:
            result.append({"role_or_other_grant_present": True})
    return result


def inspect_target(cursor, username):
    cursor.execute("START TRANSACTION READ ONLY")
    try:
        cursor.execute("SELECT @@GLOBAL.local_infile,@@GLOBAL.require_secure_transport")
        local_infile, secure_transport = cursor.fetchone()
        cursor.execute("SELECT table_name FROM information_schema.tables WHERE table_schema=%s", (TARGET_SCHEMA,))
        runtime.require(sorted(row[0] for row in cursor.fetchall()) == TABLES,
                        "새 시험 Target 테이블 목록 불일치")
        for name in TABLES:
            cursor.execute(f"SELECT COUNT(*) FROM `{TARGET_SCHEMA}`.`{name}`")
            runtime.require(cursor.fetchone() == (0,), "Target 업무 데이터 존재: 복제 준비 중단")
        cursor.execute("SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name=%s", (CONTROL_SCHEMA,))
        control_exists = int(cursor.fetchone()[0])
        cursor.execute("SELECT Host,ssl_type FROM mysql.user WHERE User=%s ORDER BY Host", (username,))
        accounts = cursor.fetchall()
        runtime.require(bool(accounts), "Target 복제 계정 미확인")
        grants = []
        for host, tls in accounts:
            cursor.execute("SHOW GRANTS FOR %s@%s", (username, host))
            grants.append({"host": host, "ssl_requirement": tls,
                           "direct_grants": summarize_grants(cursor.fetchall())})
        return {"local_infile": int(local_infile), "require_secure_transport": int(secure_transport),
                "target_tables": 16, "target_total_rows": 0,
                "proposed_control_schema": CONTROL_SCHEMA, "control_schema_exists": bool(control_exists),
                "dms_target_user": username, "accounts": grants,
                "limitation": "Direct grants only; actual DMS login/effective role privileges not verified"}
    finally:
        cursor.execute("ROLLBACK")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True)
    parser.add_argument("--local-port", type=int, default=13306)
    args = parser.parse_args()
    runtime.require(1 <= args.local_port <= 65535, "포트 범위 오류")
    directory = runtime.protected_directory(args.directory)
    master = helper.management_credentials()
    tasks = runtime.aws("dms", "describe-replication-tasks", "--without-settings", "--filters",
                        "Name=replication-task-id,Values=" + BASE_TASK)["ReplicationTasks"]
    runtime.require(len(tasks) == 1, "기존 DMS Task 식별 실패")
    task = tasks[0]
    runtime.require(task["Status"] == "stopped" and task["MigrationType"] == "full-load-and-cdc"
                    and task["SourceEndpointArn"] == SOURCE_ENDPOINT
                    and task["TargetEndpointArn"] == TARGET_ENDPOINT
                    and task["ReplicationInstanceArn"] == INSTANCE, "기존 DMS Task 상태/대상 변경")
    source = endpoint(SOURCE_ENDPOINT, "SOURCE")
    target = endpoint(TARGET_ENDPOINT, "TARGET")
    runtime.require(target.get("SslMode") == "verify-full", "Target TLS 검증 설정 불일치")
    settings = target.get("MySQLSettings") or {}
    secret_id = settings.get("SecretsManagerSecretId", "")
    runtime.require(secret_id.startswith("arn:aws:secretsmanager:ap-northeast-2:" + runtime.ACCOUNT
                                         + ":secret:seonjalal/staging/migration/target-"),
                    "리허설 Target Secret 범위 불일치")
    response = runtime.aws("secretsmanager", "get-secret-value", "--secret-id", secret_id,
                           "--version-stage", "AWSCURRENT")
    payload = json.loads(response["SecretString"])
    # 기존 Secret은 DB 접속 대상·사용자명 식별에만 메모리에서 사용. 비밀번호 저장/출력 없음.
    runtime.require(response["ARN"] == secret_id and payload.get("host") == runtime.DB_HOST
                    and str(payload.get("port")) == "3306", "Target Secret host/port 불일치")
    username = payload.get("username", "")
    runtime.require(re.fullmatch(r"[A-Za-z0-9_]{1,32}", username) is not None, "복제 사용자 형식 확인 필요")
    with runtime.connect_db(directory, args.local_port, master["username"], master["password"]) as connection:
        with connection.cursor() as cursor:
            db = inspect_target(cursor, username)
    print(json.dumps({"base_task_status": "stopped", "source": endpoint_summary(source),
                      "target": endpoint_summary(target), "database": db}, ensure_ascii=False, indent=2))
    print("DMS_ACCESS_READ_OK_NO_DB_SECRET_OR_DMS_CHANGE")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        detail = str(error) if isinstance(error, runtime.SafetyStop) else type(error).__name__
        raise SystemExit("STOP: " + detail)
