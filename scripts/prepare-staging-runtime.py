#!/usr/bin/env python3
"""승인된 리허설 계정/Secret 초기화. prepare는 로컬만, apply는 실제 변경."""
import argparse
import base64
import json
import os
from pathlib import Path
import re
import secrets
import socket
import ssl
import stat
import subprocess
import sys
import tempfile
import uuid

ACCOUNT = "721744297924"
PROFILE = "seonjalal-production"
REGION = "ap-northeast-2"
DB_ID = "seonjalal-v2-migration-rehearsal"
DB_HOST = "seonjalal-v2-migration-rehearsal.cbqckkusw4ke.ap-northeast-2.rds.amazonaws.com"
DB_NAME = "seonjalal_develop"
APP_USER = "seonjalal_staging_app"
APP_HOST = "10.20.%"
SECRET_ARN = "arn:aws:secretsmanager:ap-northeast-2:721744297924:secret:seonjalal/staging/backend/runtime-DFJ1cU"
CA_URL = "https://truststore.pki.rds.amazonaws.com/ap-northeast-2/ap-northeast-2-bundle.pem"
PHASE = "start"


class SafetyStop(RuntimeError):
    """외부 예외 원문과 구분하는, 비밀값 없는 자체 오류."""


def require(condition, message):
    if not condition:
        raise SafetyStop(message)


def secure_write(path, value):
    # 원자적 교체로 중간 실패에도 복구 정보 보존. 경로는 보호 폴더 내부만 사용.
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(value, stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def protected_directory(value):
    path = Path(value)
    require(not path.is_symlink(), "보호 폴더의 심볼릭 링크 사용 금지")
    path = path.resolve()
    require(path.parent == Path("/private/tmp").resolve(), "전용 /private/tmp 폴더 필요")
    require(path.name.startswith("seonjalal-staging-runtime."), "예상한 보호 폴더 이름 필요")
    info = path.stat()
    require(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o700, "보호 폴더 소유자/권한 불일치")
    return path


def new_state():
    return {
        "account": ACCOUNT, "secret_arn": SECRET_ARN,
        "request_token": str(uuid.uuid4()), "create_requested": False,
        "db_user_created": False,
        "payload": {
            "DB_PASSWORD": secrets.token_urlsafe(48),
            "JWT_SECRET_BASE64": base64.b64encode(secrets.token_bytes(64)).decode(),
            "LOGIN_RATE_LIMIT_HMAC_SECRET_BASE64": base64.b64encode(secrets.token_bytes(32)).decode(),
            "PROFILING_SERVICE_TOKEN": secrets.token_urlsafe(32),
        },
    }


def run_cli(command, body=None):
    if body is None:
        return subprocess.run(command, text=True, capture_output=True)
    # 일부 CLI 환경에서 /dev/stdin JSON 로드 실패. 전용0700 폴더/0600 파일 사용.
    # argv에는 비밀값 대신 임시 경로만 전달하고, CLI 종료 후 임시 파일 제거.
    with tempfile.TemporaryDirectory(prefix="seonjalal-aws-input.", dir="/private/tmp") as value:
        directory = Path(value)
        os.chmod(directory, 0o700)
        path = directory / "request.json"
        secure_write(path, body)
        return subprocess.run([*command, "--cli-input-json", "file://" + str(path)],
                              text=True, capture_output=True)


def aws(service, operation, *arguments, body=None):
    command = ["aws", "--profile", PROFILE, "--region", REGION,
               "--no-cli-pager", "--no-cli-auto-prompt", "--output", "json", service, operation, *arguments]
    result = run_cli(command, body)
    if result.returncode:
        # stderr에 입력 내용이 포함될 수 있어 오류 코드만 표시.
        match = re.search(r"\(([A-Za-z0-9]+)\)", result.stderr)
        code = match.group(1) if match else "CLI_ERROR"
        detail = ""
        if service == "secretsmanager" and operation == "put-secret-value" and body is not None:
            try:
                detail = " " + redact_cli_error(result.stderr, {"payload": json.loads(body["SecretString"])})
            except (ValueError, TypeError, AttributeError, KeyError):
                # 입력 자체가 비정상이면 원문 메시지를 출력하지 않는 쪽으로 실패.
                detail = " (요청 형식 확인 필요, 상세 원문 비공개)"
        raise SafetyStop(f"AWS {service}/{operation}: {code}{detail}")
    return json.loads(result.stdout)


def connect_db(directory, port, username, password):
    import pymysql
    context = ssl.create_default_context(cafile=str(directory / "rds-ca.pem"))
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    # 실제 TCP는 SSM localhost 터널, 인증서 hostname 검증은 RDS 원래 주소로 유지.
    connection = pymysql.Connection(
        host=DB_HOST, port=3306, user=username, password=password,
        ssl=context, defer_connect=True, autocommit=True,
        connect_timeout=10, read_timeout=20, write_timeout=20,
        local_infile=False,
    )
    transport = socket.create_connection(("127.0.0.1", port), timeout=10)
    try:
        connection.connect(sock=transport)
        require(isinstance(connection._sock, ssl.SSLSocket), "TLS 연결 필수")
        return connection
    except Exception:
        transport.close()
        raise


def grants_are_safe(rows):
    for row in rows:
        grant = row[0]
        if " WITH GRANT OPTION" in grant:
            return False
        match = re.fullmatch(r"GRANT (.+) ON (.+) TO .+", grant)
        if not match:
            return False
        privileges, target = match.groups()
        if privileges == "USAGE" and target == "*.*":
            continue
        if target != f"`{DB_NAME}`.*":
            return False
        if not set(privileges.split(", ")) <= {"SELECT", "INSERT", "UPDATE", "DELETE"}:
            return False
    return True


def prepare():
    directory = Path(tempfile.mkdtemp(prefix="seonjalal-staging-runtime.", dir="/private/tmp"))
    os.chmod(directory, 0o700)
    secure_write(directory / "state.json", new_state())
    print("RUNTIME_PREPARE_DIR=" + str(directory), flush=True)
    # 임시 venv에 고정 버전만 설치. Mac의 시스템 Python/패키지 변경 없음.
    subprocess.run([sys.executable, "-m", "venv", str(directory / "venv")], check=True)
    subprocess.run([str(directory / "venv/bin/python"), "-m", "pip", "install",
                    "--disable-pip-version-check", "PyMySQL==1.1.2"], check=True)
    subprocess.run(["curl", "--fail", "--silent", "--show-error", "--max-time", "30",
                    "--output", str(directory / "rds-ca.pem"), CA_URL], check=True)
    ssl.create_default_context(cafile=str(directory / "rds-ca.pem"))
    print("LOCAL_PREPARE_OK_NO_AWS_OR_DB_CHANGE")


def publish_body(state):
    body = {
        "SecretId": SECRET_ARN,
        "ClientRequestToken": state["request_token"],
        "SecretString": json.dumps(state["payload"]),
    }
    require(isinstance(body["ClientRequestToken"], str) and 32 <= len(body["ClientRequestToken"]) <= 64,
            "게시 요청 Token 형식 불일치")
    require(str(uuid.UUID(body["ClientRequestToken"])) == body["ClientRequestToken"], "게시 요청 Token UUID 형식 불일치")
    require(1 <= len(body["SecretString"].encode("utf-8")) <= 65536, "게시 요청 JSON 크기 불일치")
    require(json.loads(body["SecretString"]) == state["payload"], "게시 요청 JSON 직렬화 불일치")
    return body


def redact_cli_error(message, state):
    # 입력 JSON 전체 및 개별 생성값을 제거. 원문 stderr 파일 저장/공유 금지.
    values = [json.dumps(state["payload"]), *state["payload"].values()]
    for value in sorted(values, key=len, reverse=True):
        message = message.replace(value, "[REDACTED]")
        message = message.replace(json.dumps(value)[1:-1], "[REDACTED]")
    return message[:2000]


def diagnose(directory):
    path = directory / "state.json"
    info = path.lstat()
    require(not path.is_symlink() and info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o600,
            "복구 파일 소유자/권한 불일치")
    state = json.loads(path.read_text())
    require(state["account"] == ACCOUNT and state["secret_arn"] == SECRET_ARN, "복구 대상 불일치")
    require(not state.get("completed") and "payload" in state, "이미 완료됐거나 복구 값 없음")
    publish_body(state)
    print("DB_USER_CREATION_RECORDED=" + str(bool(state.get("db_user_created"))).lower())
    print("SECRET_KEYS=" + str(len(state["payload"])))
    print("REQUEST_TOKEN_LENGTH=" + str(len(state["request_token"])))
    require(32 <= len(state["request_token"]) <= 64, "Request Token 길이 불일치")
    # CLI output skeleton은 더미 응답 자체의 길이 검증 오류를 내므로 사용하지 않음.
    # 이는 자체 입력 형식 검사이며 AWS 권한/저장 성공을 보장하지 않음.
    print("LOCAL_REQUEST_FORMAT_VALIDATION=PASS")
    print("DIAGNOSE_ONLY_NO_DB_OR_SECRET_WRITE")


def verify_runtime(directory, port):
    """기존 관리 터널로 TLS/이력/시험 기준 조회만 수행. 실제 Task 앱 로그인은 별도."""
    global PHASE
    PHASE = "runtime_readonly_preflight"
    require(1 <= port <= 65535, "로컬 포트 범위 오류")
    require(aws("sts", "get-caller-identity")["Account"] == ACCOUNT, "AWS 계정 불일치")
    db = aws("rds", "describe-db-instances", "--db-instance-identifier", DB_ID)["DBInstances"][0]
    require(db["DBInstanceStatus"] == "available" and db["Endpoint"]["Address"] == DB_HOST
            and db["Endpoint"]["Port"] == 3306, "RDS 대상/상태 불일치")
    require(db.get("MasterUserSecret", {}).get("SecretStatus") == "active", "관리 Secret 상태 확인 필요")
    secret = aws("secretsmanager", "get-secret-value", "--secret-id", SECRET_ARN)
    require(secret["ARN"] == SECRET_ARN, "앱 Secret ARN 불일치")
    payload = json.loads(secret["SecretString"])
    expected_keys = {"DB_PASSWORD", "JWT_SECRET_BASE64", "LOGIN_RATE_LIMIT_HMAC_SECRET_BASE64", "PROFILING_SERVICE_TOKEN"}
    require(set(payload) == expected_keys and all(isinstance(v, str) and v for v in payload.values()),
            "앱 Secret 키/값 형식 확인 필요")
    # 기존 V1 관리 터널의 출발 주소는 앱 계정10.20.% 범위 밖.
    # 방화벽을 넓히지 않고 기존 관리 계정으로 조회만 수행. 앱의 실제 접속 검증 아님.
    master_secret = aws("secretsmanager", "get-secret-value", "--secret-id", db["MasterUserSecret"]["SecretArn"])
    master = json.loads(master_secret["SecretString"])
    require(master["username"] == db["MasterUsername"], "관리 계정 대상 불일치")
    # 고정1.3.0의 실행 서버에서 확인한 이력. 업무 데이터 최신성/전체 DDL 검증과는 별개.
    checksums = [-277388989, -759331250, 986704585, 810214977, 2035620474,
                 1486121169, -1376361150, 1242554244, 317652465, -232005916,
                 1520669154, 530316866]
    with connect_db(directory, port, master["username"], master["password"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SHOW SESSION STATUS LIKE 'Ssl_cipher'")
            tls = cursor.fetchone()
            require(tls and tls[1], "DB 세션의 TLS 암호 미확인")
            cursor.execute(f"SHOW GRANTS FOR '{APP_USER}'@'{APP_HOST}'")
            require(grants_are_safe(cursor.fetchall()), "앱 계정 예상 밖 권한 발견")
            # 연결 이후 조회를 읽기 전용 트랜잭션으로 제한. 비즈니스 DML/DDL 없음.
            cursor.execute("START TRANSACTION READ ONLY")
            try:
                cursor.execute(f"SELECT version, checksum, success FROM `{DB_NAME}`.flyway_schema_history ORDER BY installed_rank")
                rows = cursor.fetchall()
                expected = [(str(index), checksum, 1) for index, checksum in enumerate(checksums, 1)]
                require([tuple(row) for row in rows] == expected, "1.3.0 Flyway 이력 불일치")
                cursor.execute(f"SELECT COUNT(*), MAX(id) FROM `{DB_NAME}`.gift_histories")
                gift_count, latest_id = cursor.fetchone()
                cursor.execute(f"SELECT id, quantity FROM `{DB_NAME}`.products WHERE id IN (3,4) ORDER BY id")
                stock = cursor.fetchall()
            finally:
                cursor.execute("ROLLBACK")
    print("RDS_MANAGEMENT_TLS_HOSTNAME_VERIFIED")
    print("APP_ACCOUNT_GRANTS_SAFE")
    print("FLYWAY_HISTORY_MATCHED=12")
    print("FIXTURE_GIFT_COUNT=" + str(gift_count))
    print("FIXTURE_LATEST_GIFT_ID=" + str(latest_id))
    for product_id, quantity in stock:
        print(f"FIXTURE_PRODUCT_{product_id}_STOCK={quantity}")
    print("READONLY_PREFLIGHT_OK_NO_DATA_OR_SECRET_CHANGE")
    print("ECS_APP_LOGIN_AND_JAVA_TLS_NOT_YET_VERIFIED")


def apply(directory, port):
    global PHASE
    state_path = directory / "state.json"
    info = state_path.lstat()
    require(not state_path.is_symlink() and info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == 0o600,
            "복구 파일 소유자/권한 불일치")
    state = json.loads(state_path.read_text())
    require(state["account"] == ACCOUNT and state["secret_arn"] == SECRET_ARN, "복구 파일 대상 불일치")
    require(not state.get("completed"), "이미 완료된 실행. 재실행 불필요")
    payload = state["payload"]
    require(set(payload) == set(new_state()["payload"]), "런타임 키 구성 불일치")
    require(all(isinstance(v, str) and len(v) >= 32 for v in payload.values()), "비밀값 형식 불일치")
    PHASE = "aws_preflight"
    require(aws("sts", "get-caller-identity")["Account"] == ACCOUNT, "AWS 계정 불일치")
    db = aws("rds", "describe-db-instances", "--db-instance-identifier", DB_ID)["DBInstances"][0]
    require(db["DBInstanceStatus"] == "available" and db["Endpoint"]["Address"] == DB_HOST
            and db["Endpoint"]["Port"] == 3306, "RDS 대상/상태 불일치")
    require(db.get("MasterUserSecret", {}).get("SecretStatus") == "active", "관리 Secret 상태 확인 필요")
    runtime = aws("secretsmanager", "describe-secret", "--secret-id", SECRET_ARN)
    require(runtime["ARN"] == SECRET_ARN and not runtime.get("DeletedDate"), "런타임 Secret 대상 불일치")
    existing_versions = runtime.get("VersionIdsToStages", {})
    if existing_versions:
        # 이전 Publish가 성공했지만 응답이 끊긴 경우만 동일 payload 확인 후 재개.
        require(state.get("create_requested"), "기존 런타임 Secret 값 존재. 덮어쓰기 금지")
        current = aws("secretsmanager", "get-secret-value", "--secret-id", SECRET_ARN)
        require(json.loads(current["SecretString"]) == payload, "다른 런타임 값 존재. 변경 금지")
    master = json.loads(aws("secretsmanager", "get-secret-value", "--secret-id",
                            db["MasterUserSecret"]["SecretArn"])["SecretString"])
    require(master["username"] == db["MasterUsername"] == "migrationadmin", "관리 사용자 불일치")
    PHASE = "rds_account"
    with connect_db(directory, port, master["username"], master["password"]) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name=%s", (DB_NAME,))
            require(cursor.fetchone()[0] == 1, "리허설 DB 스키마 없음")
            cursor.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=%s", (DB_NAME,))
            require(cursor.fetchone()[0] >= 16, "예상한 리허설 테이블 확인 필요")
            cursor.execute("SELECT Host FROM mysql.user WHERE User=%s", (APP_USER,))
            hosts = [row[0] for row in cursor.fetchall()]
            if hosts:
                require(hosts == [APP_HOST] and state.get("db_user_created"),
                        "생성 완료 기록 없는 동일 사용자 존재. 자동 복구 금지")
            else:
                state["create_requested"] = True
                secure_write(state_path, state)
                cursor.execute("CREATE USER %s@%s IDENTIFIED BY %s REQUIRE SSL",
                               (APP_USER, APP_HOST, payload["DB_PASSWORD"]))
                state["db_user_created"] = True
                secure_write(state_path, state)
            cursor.execute("SHOW GRANTS FOR %s@%s", (APP_USER, APP_HOST))
            require(grants_are_safe(cursor.fetchall()), "예상 밖 권한 발견. 추가 변경 중단")
            cursor.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON `{DB_NAME}`.* TO %s@%s", (APP_USER, APP_HOST))
            cursor.execute("SHOW GRANTS FOR %s@%s", (APP_USER, APP_HOST))
            require(grants_are_safe(cursor.fetchall()), "DML 권한 검사 실패")
    # 준비 터널은 기존 V1 경로 사용. 앱의 10.20.% 로그인은 Task 실행 시 별도 검증.
    print("RDS_ACCOUNT_CREATED_TLS_AND_DML_GRANTS_OK", flush=True)
    PHASE = "runtime_publish"
    if not existing_versions:
        fresh = aws("secretsmanager", "describe-secret", "--secret-id", SECRET_ARN)
        require(not fresh.get("VersionIdsToStages"), "동시 Secret 변경 감지. 게시 중단")
        aws("secretsmanager", "put-secret-value", body=publish_body(state))
    verified = aws("secretsmanager", "get-secret-value", "--secret-id", SECRET_ARN)
    require(json.loads(verified["SecretString"]) == payload, "Secret 저장 검증 실패")
    # 성공 시 보호 파일에서 비밀값 제거. 실패 시에는 복구를 위해 그대로 보존.
    state.pop("payload")
    state["completed"] = True
    secure_write(state_path, state)
    print("RUNTIME_SECRET_INITIALIZED_KEYS=4")
    print("STAGING_RUNTIME_READY_NO_APP_OR_DATA_CHANGE")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "diagnose", "verify", "apply"])
    parser.add_argument("--directory")
    parser.add_argument("--local-port", type=int, default=13306)
    parser.add_argument("--approved-rehearsal-change", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    if args.action == "prepare":
        prepare()
    elif args.action == "diagnose":
        require(args.directory, "보호 폴더 필요")
        diagnose(protected_directory(args.directory))
    elif args.action == "verify":
        require(args.directory, "기존 인증서 보호 폴더 필요")
        verify_runtime(protected_directory(args.directory), args.local_port)
    else:
        require(args.approved_rehearsal_change and args.directory, "승인 플래그/보호 폴더 필요")
        apply(protected_directory(args.directory), args.local_port)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # SQL/Secret 내용을 포함할 수 있는 원문 Exception/Traceback 출력 금지.
        code = error.args[0] if error.args and isinstance(error.args[0], int) else type(error).__name__
        detail = str(error) if isinstance(error, SafetyStop) else "상세 원문 비공개"
        print(f"STOP PHASE={PHASE} CODE={code}: {detail}", file=sys.stderr)
        print("보호 폴더 유지. 재준비/기존 계정 삭제 없이 이 오류만 공유해주세요.", file=sys.stderr)
        sys.exit(1)
