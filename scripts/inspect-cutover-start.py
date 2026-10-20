#!/usr/bin/env python3
"""Mac: 새 DMS ready/실제 설정/Target/Source 카탈로그 점검. 복제 시작 없음."""
import argparse
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import shlex
import time

spec = importlib.util.spec_from_file_location(
    "dms_task", Path(__file__).with_name("prepare-cutover-dms-task.py"))
task_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(task_module)
access = task_module.access
runtime = task_module.runtime
TASK_ARN = "arn:aws:dms:ap-northeast-2:721744297924:task:OU5M6NHIS5BOHGL7KSXRWHCKNY"
SOURCE_INSTANCE = "i-0c6f5ea09b5e20859"
SOURCE_HOST = "172.31.6.212"


def require_settings(actual, expected):
    for key, value in expected.items():
        runtime.require(key in actual, "실제 Task 필수 설정 누락: " + key)
        if isinstance(value, dict):
            runtime.require(isinstance(actual[key], dict), "실제 Task 설정 형식 오류")
            require_settings(actual[key], value)
        elif isinstance(value, list) and value and all(isinstance(row, dict) and "Id" in row for row in value):
            # DMS가 기본 로그 항목을 추가해도 요청한 항목의 설정은 유지돼야 함.
            runtime.require(isinstance(actual[key], list), "실제 Task 로그 설정 형식 오류")
            by_id = {row["Id"]: row for row in actual[key]}
            for row in value:
                runtime.require(row["Id"] in by_id, "실제 Task 로그 항목 누락")
                require_settings(by_id[row["Id"]], row)
        else:
            runtime.require(actual[key] == value, "실제 Task 설정 차이: " + key)


def check_task(row):
    body = task_module.request()
    runtime.require(row["ReplicationTaskArn"] == TASK_ARN and row["Status"] == "ready",
                    "새 Task가 정확한 ready 상태인지 확인 필요: 복제 시작 금지 STATUS=" + str(row.get("Status")))
    for key in ("ReplicationTaskIdentifier", "SourceEndpointArn", "TargetEndpointArn",
                "ReplicationInstanceArn", "MigrationType"):
        runtime.require(row[key] == body[key], "새 Task 실제 대상 변경: " + key)
    runtime.require(json.loads(row["TableMappings"]) == task_module.mappings(), "실제 mapping이 계획과 다름")
    require_settings(json.loads(row["ReplicationTaskSettings"]), task_module.settings())


def check_catalog(payload):
    expected = [("seonjalal_develop", name, "BASE TABLE", "InnoDB") for name in access.TABLES]
    # SQL LIKE로 schema의 _ 확장 후보도 수집, extra table/view/schema가 있으면 중단.
    runtime.require(sorted(tuple(row) for row in payload["catalog"]) == expected,
                    "Source 카탈로그 변경/패턴 확장/VIEW 감지: 복제 시작 금지")
    runtime.require(payload["binlog"][:3] == ["1", "ROW", "FULL"], "Source binlog 설정 불일치")
    runtime.require(payload["binlog"][3:] == ["0", "0"], "Source 쓰기 차단 상태 변경")
    runtime.require(payload["scheduler_flags"] == ["false", "false"], "Source 앱 스케줄러 상태 확인 필요")
    runtime.require(payload["source_trigger_count"] == 0 and payload["source_event_count"] == 0,
                    "Source trigger/event 존재: Writer/객체 이전 범위 검토 필요")


def remote_program():
    return '''import json,subprocess,sys
from urllib.parse import urlsplit
def run(args):
    p=subprocess.run(args,capture_output=True,timeout=30)
    if p.returncode: raise RuntimeError('READ_FAILED')
    return p.stdout.decode()
def sql(query):
    return run(['docker','exec','seonjalal-database','sh','-c',
        'test -n "$MYSQL_ROOT_PASSWORD" || exit 1; export MYSQL_PWD="$MYSQL_ROOT_PASSWORD"; exec mysql --user=root --protocol=SOCKET --batch --skip-column-names --execute="$1"',
        'sh',query]).strip()
try:
    token=run(['curl','-fsS','--max-time','3','-X','PUT','-H','X-aws-ec2-metadata-token-ttl-seconds:60','http://169.254.169.254/latest/api/token'])
    instance=run(['curl','-fsS','--max-time','3','-H','X-aws-ec2-metadata-token:'+token,'http://169.254.169.254/latest/meta-data/instance-id'])
    if instance != __INSTANCE__: raise RuntimeError('WRONG_INSTANCE')
    private_ip=run(['curl','-fsS','--max-time','3','-H','X-aws-ec2-metadata-token:'+token,'http://169.254.169.254/latest/meta-data/local-ipv4'])
    if private_ip != __HOST__: raise RuntimeError('WRONG_SOURCE_PRIVATE_IP')
    app=json.loads(run(['docker','inspect','seonjalal-backend']))[0]
    env=dict(value.split('=',1) for value in app['Config']['Env'] if '=' in value)
    db=urlsplit(env.get('DB_URL','').removeprefix('jdbc:'))
    if app['State']['Status']!='running' or app['Image']!='sha256:b93ca625008ea5eb57e600dc56e033a7ca45bce8842ac76b789eb0da6563e1f2': raise RuntimeError('SOURCE_APP_CHANGED')
    if db.hostname!='database' or db.path!='/seonjalal_develop': raise RuntimeError('SOURCE_DB_CHANGED')
    query="SELECT TABLE_SCHEMA,TABLE_NAME,TABLE_TYPE,COALESCE(ENGINE,'') FROM information_schema.tables WHERE TABLE_SCHEMA LIKE 'seonjalal_develop' ORDER BY TABLE_SCHEMA,TABLE_NAME"
    catalog=[line.split('\\t') for line in sql(query).splitlines()]
    binlog=sql('SELECT @@GLOBAL.log_bin,@@GLOBAL.binlog_format,@@GLOBAL.binlog_row_image,@@GLOBAL.read_only,@@GLOBAL.super_read_only').split('\\t')
    triggers=int(sql("SELECT COUNT(*) FROM information_schema.triggers WHERE TRIGGER_SCHEMA LIKE 'seonjalal_develop'"))
    events=int(sql("SELECT COUNT(*) FROM information_schema.events WHERE EVENT_SCHEMA LIKE 'seonjalal_develop'"))
    transactions=int(sql('SELECT COUNT(*) FROM information_schema.INNODB_TRX'))
    print(json.dumps({'kind':'cutover_source_catalog_v1','catalog':catalog,'binlog':binlog,
        'scheduler_flags':[env.get('SCHEDULING_ENABLED','NOT_SET'),env.get('AI_PROFILE_SCHEDULING_ENABLED','NOT_SET')],
        'source_trigger_count':triggers,'source_event_count':events,'open_transactions_at_sample':transactions}))
except Exception:
    print('SOURCE_CATALOG_READ_FAILED_NO_RAW_OUTPUT',file=sys.stderr)
    sys.exit(1)
'''.replace("__INSTANCE__", repr(SOURCE_INSTANCE)).replace("__HOST__", repr(SOURCE_HOST))


def collect_catalog(directory):
    request = {"DocumentName": "AWS-RunShellScript", "InstanceIds": [SOURCE_INSTANCE],
               "Comment": "Read-only source metadata before isolated DMS start; no data/service changes",
               "TimeoutSeconds": 120, "Parameters": {"commands": ["python3 -c " + shlex.quote(remote_program())],
                                                      "executionTimeout": ["120"]}}
    identifier = runtime.aws("ssm", "send-command", body=request)["Command"]["CommandId"]
    runtime.secure_write(directory / "cutover-source-catalog-command.json", {"command_id": identifier})
    print("SOURCE_CATALOG_READ_COMMAND_ID=" + identifier, flush=True)
    deadline = time.monotonic() + 150
    while time.monotonic() < deadline:
        try:
            response = runtime.aws("ssm", "get-command-invocation", "--command-id", identifier,
                                   "--instance-id", SOURCE_INSTANCE)
        except runtime.SafetyStop as error:
            if "InvocationDoesNotExist" not in str(error):
                raise
            time.sleep(2)
            continue
        if response["Status"] == "Success":
            runtime.require(response["ResponseCode"] == 0, "Source 카탈로그 조회 종료 코드 오류")
            payload = json.loads(response["StandardOutputContent"])
            runtime.require(payload.get("kind") == "cutover_source_catalog_v1", "Source 응답 형식 오류")
            check_catalog(payload)
            return payload
        runtime.require(response["Status"] in ("Pending", "InProgress", "Delayed"),
                        "Source 카탈로그 조회 실패: CommandId 확인, 원문 공유 금지")
        time.sleep(2)
    raise runtime.SafetyStop("Source 조회 대기 초과: 재전송 전 CommandId 상태 확인")


def check_secret_host(endpoint, suffix, expected_host):
    mysql = endpoint.get("MySQLSettings") or {}
    secret = mysql.get("SecretsManagerSecretId", "")
    runtime.require(secret.startswith("arn:aws:secretsmanager:ap-northeast-2:" + runtime.ACCOUNT
                                      + ":secret:seonjalal/staging/migration/" + suffix + "-"),
                    "DMS Secret 범위 변경")
    value = runtime.aws("secretsmanager", "get-secret-value", "--secret-id", secret,
                        "--version-stage", "AWSCURRENT")
    payload = json.loads(value["SecretString"])
    runtime.require(value["ARN"] == secret and payload.get("host") == expected_host
                    and str(payload.get("port")) == "3306", "실제 Secret 연결 대상 변경")
    if suffix == "target":
        runtime.require(payload.get("username") == task_module.permissions.USER, "Target 복제 사용자 변경")
    return {"host_verified": True, "version_id": value["VersionId"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True)
    parser.add_argument("--local-port", type=int, default=13306)
    parser.add_argument("--approved-source-catalog-read", action="store_true")
    args = parser.parse_args()
    runtime.require(args.approved_source_catalog_read, "Source 메타데이터 원격 읽기 승인 옵션 필요")
    runtime.require(1 <= args.local_port <= 65535, "포트 범위 오류")
    directory = runtime.protected_directory(args.directory)
    master = access.helper.management_credentials()
    rows = runtime.aws("dms", "describe-replication-tasks", "--filters",
                       "Name=replication-task-arn,Values=" + TASK_ARN)["ReplicationTasks"]
    runtime.require(len(rows) == 1, "새 Task 식별 실패")
    check_task(rows[0])
    source = access.endpoint(access.SOURCE_ENDPOINT, "SOURCE")
    target = access.endpoint(access.TARGET_ENDPOINT, "TARGET")
    task_module.check_endpoint(source, "SOURCE")
    task_module.check_endpoint(target, "TARGET")
    secrets = {"source": check_secret_host(source, "source", SOURCE_HOST),
               "target": check_secret_host(target, "target", runtime.DB_HOST)}
    with runtime.connect_db(directory, args.local_port, master["username"], master["password"]) as connection:
        with connection.cursor() as cursor:
            metadata = access.inspect_target(cursor, task_module.permissions.USER)
            runtime.require(metadata["control_schema_exists"] and metadata["local_infile"] == 1,
                            "Target 준비 상태 변경")
            cursor.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=%s",
                           (access.CONTROL_SCHEMA,))
            runtime.require(cursor.fetchone() == (0,), "새 제어 DB 초기 상태 변경")
    catalog = collect_catalog(directory)
    report = {"checked_at_utc": datetime.now(timezone.utc).isoformat(), "task_arn": TASK_ARN,
              "task_status": "ready", "settings_verified": True, "source_catalog_tables": 16,
              "source_catalog": catalog, "target_tables": 16, "target_rows": 0,
              "secret_versions": secrets, "source_ssl": "none", "target_ssl": "verify-full",
              "source_schema_freeze_confirmation_required": True,
              "source_tls_test_data_exception_approval_required": True, "replication_started": False}
    runtime.secure_write(directory / "cutover-start-preflight.json", report)
    summary = {key: value for key, value in report.items() if key not in ("source_catalog", "secret_versions")}
    summary["source_open_transactions_at_sample"] = catalog["open_transactions_at_sample"]
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("DMS_START_PREFLIGHT_OK_NO_REPLICATION_OR_DB_CHANGE")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        detail = str(error) if isinstance(error, runtime.SafetyStop) else type(error).__name__
        raise SystemExit("STOP: " + detail)
