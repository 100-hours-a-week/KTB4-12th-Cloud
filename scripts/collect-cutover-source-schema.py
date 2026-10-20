#!/usr/bin/env python3
"""Mac: 승인된 비운영 Source의 구조만 SSM으로 읽고 보호된 로컬 파일로 수집."""
import argparse
import base64
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import tempfile
import time

spec = importlib.util.spec_from_file_location(
    "staging_runtime", Path(__file__).with_name("prepare-staging-runtime.py"))
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)
INSTANCE = "i-0c6f5ea09b5e20859"
SCHEMA = "seonjalal_develop"
TABLES = sorted("categories flyway_schema_history friends gift_histories "
                "login_email_failure_limits login_ip_rate_limits product_images "
                "product_views products recipient_profiles recipient_recommended_products "
                "term_consents terms user_dislike_categories user_sessions users".split())


def validate_schema(sql):
    names = re.findall(r"^CREATE TABLE `([a-z0-9_]+)`", sql, re.M)
    runtime.require(sorted(names) == TABLES, "Source 테이블 목록/개수 불일치")
    # 검사 목적은 구조 전용 Artifact 수집. DB에 실행 가능한 안전성 전체 증명 아님.
    forbidden = (r"\b(?:INSERT\s+INTO|REPLACE\s+INTO|DROP\s+(?:TABLE|DATABASE)|"
                 r"CREATE\s+DATABASE|USE\s+`|DEFINER\s*=|GRANT\s+)" )
    runtime.require(re.search(forbidden, sql, re.I) is None, "예상 밖 데이터/객체/권한 구문")
    runtime.require("`seonjalal_develop`." not in sql, "기존 schema 명시 참조: 재검토 필요")
    runtime.require(0 < len(sql.encode()) <= 256000, "구조 파일 크기 범위 오류")
    return names


def remote_program():
    # 원격 파일 생성·설정 변경·DB DML/DDL 없음. stderr/환경변수 원문 출력 금지.
    return '''import base64,gzip,hashlib,json,subprocess,sys
from urllib.parse import urlsplit
def run(args):
    result=subprocess.run(args,capture_output=True,timeout=45)
    if result.returncode: raise RuntimeError('SOURCE_READ_FAILED')
    return result.stdout
try:
    token=run(['curl','-fsS','--max-time','3','-X','PUT','-H','X-aws-ec2-metadata-token-ttl-seconds:60','http://169.254.169.254/latest/api/token']).decode()
    identity=run(['curl','-fsS','--max-time','3','-H','X-aws-ec2-metadata-token:'+token,'http://169.254.169.254/latest/meta-data/instance-id']).decode()
    if identity != __INSTANCE__: raise RuntimeError('WRONG_SOURCE_INSTANCE')
    app=json.loads(run(['docker','inspect','seonjalal-backend']))[0]
    env=dict(value.split('=',1) for value in app['Config']['Env'] if '=' in value)
    db=urlsplit(env.get('DB_URL','').removeprefix('jdbc:'))
    if app['State']['Status']!='running' or app['Image']!='sha256:b93ca625008ea5eb57e600dc56e033a7ca45bce8842ac76b789eb0da6563e1f2': raise RuntimeError('SOURCE_IMAGE_OR_STATE_CHANGED')
    if db.hostname!='database' or db.path!='/seonjalal_develop': raise RuntimeError('SOURCE_DB_CHANGED')
    command=['docker','exec','seonjalal-database','sh','-c',
        'test -n "$MYSQL_ROOT_PASSWORD" || exit 1; export MYSQL_PWD="$MYSQL_ROOT_PASSWORD"; exec mysqldump --user=root --no-data --skip-comments --skip-add-drop-table --no-tablespaces --set-gtid-purged=OFF --skip-triggers --routines=false --events=false --column-statistics=0 --single-transaction --skip-lock-tables "$@"',
        'sh','seonjalal_develop',*__TABLES__]
    data=run(command)
    payload={'kind':'cutover_source_schema_v1','sha256':hashlib.sha256(data).hexdigest(),'gzip_base64':base64.b64encode(gzip.compress(data)).decode()}
    output=json.dumps(payload)
    if len(output)>18000: raise RuntimeError('SCHEMA_OUTPUT_TOO_LARGE')
    print(output)
except Exception:
    print('STOP_SOURCE_SCHEMA_READ_ONLY_FAILED',file=sys.stderr)
    sys.exit(1)
'''.replace("__INSTANCE__", repr(INSTANCE)).replace("__TABLES__", repr(TABLES))


def request():
    return {"DocumentName": "AWS-RunShellScript", "InstanceIds": [INSTANCE],
            "Comment": "Read-only fixed V1 schema collection; no data or service changes",
            "TimeoutSeconds": 120,
            "Parameters": {"commands": ["python3 -c " + shlex.quote(remote_program())],
                           "executionTimeout": ["120"]}}


def save_payload(output, directory):
    payload = json.loads(output)
    runtime.require(payload.get("kind") == "cutover_source_schema_v1", "응답 유형 오류")
    packed = base64.b64decode(payload["gzip_base64"], validate=True)
    runtime.require(len(packed) <= 18000, "압축 파일 범위 오류")
    # 신뢰 대상 SSM 응답이라도 최대 복원 길이를 제한.
    import io
    with gzip.GzipFile(fileobj=io.BytesIO(packed)) as stream:
        raw = stream.read(256001)
    runtime.require(len(raw) <= 256000, "구조 파일 범위 초과")
    runtime.require(hashlib.sha256(raw).hexdigest() == payload["sha256"], "구조 파일 해시 불일치")
    validate_schema(raw.decode("utf-8"))
    path = directory / "source-schema.sql"
    with path.open("xb") as stream:
        stream.write(raw)
    os.chmod(path, 0o600)
    return path, payload["sha256"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approved-source-schema-read", action="store_true")
    args = parser.parse_args()
    runtime.require(args.approved_source_schema_read, "Source 원격 구조 읽기 승인 옵션 필요")
    runtime.require(runtime.aws("sts", "get-caller-identity")["Account"] == runtime.ACCOUNT,
                    "계정 불일치")
    os.umask(0o077)
    directory = Path(tempfile.mkdtemp(prefix="seonjalal-cutover-source.", dir="/private/tmp"))
    command = runtime.aws("ssm", "send-command", body=request())["Command"]["CommandId"]
    runtime.secure_write(directory / "request-record.json", {"command_id": command, "instance": INSTANCE})
    print("READ_COMMAND_ID=" + command + "\nRESULT_DIR=" + str(directory), flush=True)
    deadline = time.monotonic() + 150
    while time.monotonic() < deadline:
        try:
            result = runtime.aws("ssm", "get-command-invocation", "--command-id", command,
                                 "--instance-id", INSTANCE)
        except runtime.SafetyStop as error:
            if "InvocationDoesNotExist" not in str(error):
                raise
            time.sleep(2)
            continue
        if result["Status"] == "Success":
            runtime.require(result["ResponseCode"] == 0, "원격 종료 코드 오류")
            path, digest = save_payload(result.get("StandardOutputContent", ""), directory)
            print("SOURCE_SCHEMA_FILE=" + str(path))
            print("SOURCE_SCHEMA_SHA256=" + digest)
            print("TABLES=16 ROW_DATA=0 NO_DB_OR_SERVICE_CHANGE")
            return
        runtime.require(result["Status"] in ("Pending", "InProgress", "Delayed"),
                        "Source 구조 조회 실패: CommandId로 상태 확인, 원문 공유 금지")
        time.sleep(2)
    raise runtime.SafetyStop("원격 결과 대기 초과: 재실행 전 READ_COMMAND_ID 상태 확인")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        detail = str(error) if isinstance(error, runtime.SafetyStop) else type(error).__name__
        raise SystemExit("STOP: " + detail)
