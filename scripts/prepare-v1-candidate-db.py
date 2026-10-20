#!/usr/bin/env python3
"""기본은 읽기 검사. 명시적 이중 승인 옵션에서만 후보 Host 계정 추가."""
import argparse
import importlib.util
import json
from pathlib import Path
import sys

spec = importlib.util.spec_from_file_location(
    "staging_runtime", Path(__file__).with_name("prepare-staging-runtime.py")
)
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)
SOURCE_HOST = "172.31.6.212"


def management_credentials():
    check = runtime.require
    check(runtime.aws("sts", "get-caller-identity")["Account"] == runtime.ACCOUNT,
          "AWS 계정 불일치")
    db = runtime.aws("rds", "describe-db-instances", "--db-instance-identifier",
                     runtime.DB_ID)["DBInstances"][0]
    check(db["DBInstanceStatus"] == "available" and
          db["Endpoint"]["Address"] == runtime.DB_HOST and
          db["Endpoint"]["Port"] == 3306, "리허설 RDS 대상/상태 불일치")
    check(db.get("MasterUserSecret", {}).get("SecretStatus") == "active",
          "관리 Secret 상태 불일치")
    # 관리 비밀번호는 Mac 메모리에만 유지. Source EC2로 전달하지 않음.
    master = json.loads(runtime.aws(
        "secretsmanager", "get-secret-value", "--secret-id",
        db["MasterUserSecret"]["SecretArn"], "--version-stage", "AWSCURRENT"
    )["SecretString"])
    check(master["username"] == db["MasterUsername"], "관리 계정 불일치")
    return master


def preflight(directory, port):
    check = runtime.require
    check(1 <= port <= 65535, "로컬 포트 범위 오류")
    master = management_credentials()
    with runtime.connect_db(directory, port, master["username"], master["password"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT User, Host, ssl_type FROM mysql.user WHERE User=%s ORDER BY Host",
                           (runtime.APP_USER,))
            accounts = cursor.fetchall()
            check(len(accounts) == 1 and accounts[0][1] == runtime.APP_HOST,
                  "기존 앱 계정 Host 구성이 예상과 다름. 자동 변경 중단")
            check(accounts[0][2] == "ANY", "기존 앱 계정 REQUIRE SSL 확인 필요")
            cursor.execute("SHOW GRANTS FOR %s@%s", (runtime.APP_USER, runtime.APP_HOST))
            check(runtime.grants_are_safe(cursor.fetchall()), "기존 앱 계정 예상 밖 권한")
            # 변경 전에 Fixture 기준을 재확인. 기존 MySQL은 조회·수정하지 않음.
            cursor.execute("START TRANSACTION READ ONLY")
            try:
                cursor.execute(f"SELECT COUNT(*), MAX(id) FROM `{runtime.DB_NAME}`.gift_histories")
                gift_count, latest_id = cursor.fetchone()
                cursor.execute(f"SELECT id, quantity FROM `{runtime.DB_NAME}`.products WHERE id IN (3,4) ORDER BY id")
                stocks = [tuple(row) for row in cursor.fetchall()]
                check((gift_count, latest_id) == (5022, 5022) and stocks == [(3, 59), (4, 87)],
                      "독립 Fixture 기준 불일치. DB 권한/후보 변경 전 조사 필요")
            finally:
                cursor.execute("ROLLBACK")
    print("RDS_MANAGEMENT_TLS_HOSTNAME_VERIFIED")
    print("EXISTING_ECS_ACCOUNT_SSL_AND_GRANTS_SAFE")
    print("V1_CANDIDATE_HOST_ACCOUNT=ABSENT")
    print("PROPOSED_ACCOUNT_HOST=" + SOURCE_HOST)
    print("FIXTURE_GIFT_COUNT=5022")
    print("FIXTURE_PRODUCT_3_STOCK=59")
    print("FIXTURE_PRODUCT_4_STOCK=87")
    print("PREFLIGHT_OK_NO_DB_OR_SECRET_WRITE")


def create_candidate_account(cursor, password):
    # IF NOT EXISTS/ALTER를 사용하지 않아 기존 Host 계정 덮어쓰기 방지.
    cursor.execute("CREATE USER %s@%s IDENTIFIED BY %s REQUIRE SSL",
                   (runtime.APP_USER, SOURCE_HOST, password))
    cursor.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON `{runtime.DB_NAME}`.* TO %s@%s",
                   (runtime.APP_USER, SOURCE_HOST))
    cursor.execute("SELECT ssl_type FROM mysql.user WHERE User=%s AND Host=%s",
                   (runtime.APP_USER, SOURCE_HOST))
    runtime.require(cursor.fetchone() == ("ANY",), "후보 REQUIRE SSL 검증 실패")
    cursor.execute("SHOW GRANTS FOR %s@%s", (runtime.APP_USER, SOURCE_HOST))
    grants = cursor.fetchall()
    runtime.require(runtime.grants_are_safe(grants) and any(
        "SELECT, INSERT, UPDATE, DELETE" in row[0] for row in grants
    ), "후보 DML 권한 검증 실패")


def apply_candidate(directory, port):
    # 검사 실패 시 CREATE 전 중단. 기존 Fixture·ECS 계정 조건 재확인.
    preflight(directory, port)
    record_path = directory / "v1-candidate-db-account.json"
    runtime.require(not record_path.exists(), "이전 변경 기록 존재. 자동 재실행 금지")
    secret = runtime.aws("secretsmanager", "get-secret-value", "--secret-id",
                         runtime.SECRET_ARN, "--version-stage", "AWSCURRENT")
    runtime.require(secret["ARN"] == runtime.SECRET_ARN, "시험 Secret 대상 불일치")
    payload = json.loads(secret["SecretString"])
    password = payload.get("DB_PASSWORD")
    runtime.require(isinstance(password, str) and len(password) >= 32, "시험 비밀번호 형식 확인 필요")
    master = management_credentials()
    record = {
        "account": runtime.ACCOUNT, "db": runtime.DB_ID,
        "user": runtime.APP_USER, "host": SOURCE_HOST,
        "secret_version_id": secret["VersionId"], "status": "create_requested",
    }
    # CREATE/GRANT는 자동 커밋이므로 실패 시 부분 생성 가능. 비밀 없는 의도 기록 보존.
    runtime.secure_write(record_path, record)
    print("CHANGE_RECORD=" + str(record_path), flush=True)
    with runtime.connect_db(directory, port, master["username"], master["password"]) as connection:
        with connection.cursor() as cursor:
            create_candidate_account(cursor, password)
    record["status"] = "created_and_granted"
    runtime.secure_write(record_path, record)
    with runtime.connect_db(directory, port, runtime.APP_USER, password) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT CURRENT_USER()")
            runtime.require(cursor.fetchone() == (runtime.APP_USER + "@" + SOURCE_HOST,),
                            "후보 실제 인증 Host 불일치")
            cursor.execute("SHOW SESSION STATUS LIKE 'Ssl_cipher'")
            tls = cursor.fetchone()
            runtime.require(tls and tls[1], "후보 TLS 세션 확인 필요")
            cursor.execute("START TRANSACTION READ ONLY")
            try:
                cursor.execute(f"SELECT COUNT(*), MAX(id) FROM `{runtime.DB_NAME}`.gift_histories")
                runtime.require(cursor.fetchone() == (5022, 5022), "Fixture 기준 불일치")
            finally:
                cursor.execute("ROLLBACK")
    record["status"] = "login_tls_read_verified"
    runtime.secure_write(record_path, record)
    print("V1_CANDIDATE_ACCOUNT_CREATED_SSL_DML_ONLY")
    print("V1_CANDIDATE_LOGIN_TLS_HOSTNAME_AND_READ_VERIFIED")
    print("NO_BUSINESS_DATA_OR_SECRET_CHANGE")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True)
    parser.add_argument("--local-port", type=int, default=13306)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--approved-rehearsal-change", action="store_true")
    args = parser.parse_args()
    runtime.require(not args.apply or args.approved_rehearsal_change,
                    "DB 계정 추가는 별도 승인 옵션 필요")
    directory = runtime.protected_directory(args.directory)
    if args.apply:
        apply_candidate(directory, args.local_port)
    else:
        preflight(directory, args.local_port)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # SQL/비밀번호가 포함될 수 있는 원문 예외와 Traceback 출력 금지.
        code = error.args[0] if error.args and isinstance(error.args[0], int) else type(error).__name__
        detail = str(error) if isinstance(error, runtime.SafetyStop) else "상세 원문 비공개"
        print(f"STOP CODE={code}: {detail}", file=sys.stderr)
        sys.exit(1)
