#!/usr/bin/env python3
"""Mac: 새 DMS Task 한 개 생성 계획. 승인 실행도 복제 시작·기존 Task 변경 없음."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "dms_permissions", Path(__file__).with_name("prepare-cutover-dms-permissions.py"))
permissions = importlib.util.module_from_spec(spec)
spec.loader.exec_module(permissions)
access = permissions.access
runtime = access.runtime
TASK = "seonjalal-db-low-downtime-20261019"


def mappings():
    rules = [{"rule-type": "selection", "rule-id": str(index), "rule-name": "include-" + name,
              "object-locator": {"schema-name": "seonjalal_develop", "table-name": name},
              "rule-action": "include", "filters": []}
             for index, name in enumerate(access.TABLES, 1)]
    rules.append({"rule-type": "transformation", "rule-id": "100", "rule-name": "isolated-target-schema",
                  "rule-target": "schema", "rule-action": "rename",
                  "object-locator": {"schema-name": "seonjalal_develop"}, "value": access.TARGET_SCHEMA})
    return {"rules": rules}


def settings():
    return {
        "TargetMetadata": {"TargetSchema": "", "SupportLobs": True, "FullLobMode": False,
                           "LimitedSizeLobMode": True, "LobMaxSize": 256, "BatchApplyEnabled": False},
        "FullLoadSettings": {"TargetTablePrepMode": "DO_NOTHING", "CreatePkAfterFullLoad": False,
                             "MaxFullLoadSubTasks": 2, "TransactionConsistencyTimeout": 600,
                             "CommitRate": 1000, "StopTaskCachedChangesApplied": False,
                             "StopTaskCachedChangesNotApplied": False},
        "ValidationSettings": {"EnableValidation": True, "ValidationOnly": False,
                               "ThreadCount": 2, "SkipLobColumns": False, "ValidationPartialLobSize": 0},
        "ControlTablesSettings": {"ControlSchema": access.CONTROL_SCHEMA,
                                  "StatusTableEnabled": True, "SuspendedTablesTableEnabled": True,
                                  "HistoryTableEnabled": True, "HistoryTimeslotInMinutes": 5},
        "Logging": {"EnableLogging": True, "LogComponents": [
            {"Id": name, "Severity": "LOGGER_SEVERITY_DEFAULT"}
            for name in ("SOURCE_UNLOAD", "TARGET_LOAD", "SOURCE_CAPTURE", "TARGET_APPLY")]},
        "ChangeProcessingDdlHandlingPolicy": {"HandleSourceTableDropped": False,
                                              "HandleSourceTableTruncated": False,
                                              "HandleSourceTableAltered": False},
        "ErrorBehavior": {"DataErrorPolicy": "STOP_TASK", "DataTruncationErrorPolicy": "STOP_TASK",
                          "TableErrorPolicy": "STOP_TASK", "ApplyErrorDeletePolicy": "STOP_TASK",
                          "ApplyErrorInsertPolicy": "STOP_TASK", "ApplyErrorUpdatePolicy": "STOP_TASK",
                          "FailOnNoTablesCaptured": True, "FullLoadIgnoreConflicts": False,
                          "RecoverableErrorCount": 0},
    }


def request():
    # 비밀번호/Secret 값 없이 기존 Endpoint ARN만 사용. Start/Modify API 없음.
    return {"ReplicationTaskIdentifier": TASK, "SourceEndpointArn": access.SOURCE_ENDPOINT,
            "TargetEndpointArn": access.TARGET_ENDPOINT, "ReplicationInstanceArn": access.INSTANCE,
            "MigrationType": "full-load-and-cdc",
            "TableMappings": json.dumps(mappings(), sort_keys=True),
            "ReplicationTaskSettings": json.dumps(settings(), sort_keys=True),
            "Tags": [{"Key": "Environment", "Value": "staging"},
                     {"Key": "Purpose", "Value": "isolated-low-downtime-cutover"}]}


def digest(body):
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def check_endpoint(item, kind):
    summary = access.endpoint_summary(item)
    runtime.require(summary["secret_backed"] and summary["database_name"] is None,
                    "Endpoint Secret/강제 DB명 상태 변경")
    runtime.require(not summary["other_extra_attributes_present"]
                    and not summary["after_connect_script_present"], "예상 밖 Endpoint 실행 속성")
    if kind == "TARGET":
        runtime.require(summary["ssl_mode"] == "verify-full" and summary["fk_disable_initstmt"]
                        and bool(item.get("CertificateArn")), "Target TLS/CA/FK 설정 변경")
    else:
        runtime.require(summary["ssl_mode"] == "none", "Source TLS 상태 변경: 재검토 필요")
    return summary


def preflight(directory, port):
    master = access.helper.management_credentials()
    tasks = runtime.aws("dms", "describe-replication-tasks", "--without-settings")["ReplicationTasks"]
    runtime.require(not any(row["ReplicationTaskIdentifier"] == TASK for row in tasks),
                    "동일 이름 Task 존재: 새로 생성/수정하지 않고 상태 확인")
    original = [row for row in tasks if row["ReplicationTaskIdentifier"] == access.BASE_TASK]
    runtime.require(len(original) == 1 and original[0]["Status"] == "stopped"
                    and original[0]["SourceEndpointArn"] == access.SOURCE_ENDPOINT
                    and original[0]["TargetEndpointArn"] == access.TARGET_ENDPOINT
                    and original[0]["ReplicationInstanceArn"] == access.INSTANCE, "기존 Task 상태/대상 변경")
    runtime.require(all(row["Status"] == "stopped" for row in tasks
                        if row["TargetEndpointArn"] == access.TARGET_ENDPOINT),
                    "공유 Target Endpoint의 다른 작업 실행 중")
    source = check_endpoint(access.endpoint(access.SOURCE_ENDPOINT, "SOURCE"), "SOURCE")
    target = check_endpoint(access.endpoint(access.TARGET_ENDPOINT, "TARGET"), "TARGET")
    instances = runtime.aws("dms", "describe-replication-instances", "--filters",
                            "Name=replication-instance-arn,Values=" + access.INSTANCE)["ReplicationInstances"]
    runtime.require(len(instances) == 1 and instances[0]["ReplicationInstanceStatus"] == "available",
                    "복제 인스턴스 available 확인 필요")
    with runtime.connect_db(directory, port, master["username"], master["password"]) as connection:
        with connection.cursor() as cursor:
            metadata = access.inspect_target(cursor, permissions.USER)
            runtime.require(metadata["control_schema_exists"] and metadata["local_infile"] == 1,
                            "제어 DB/local_infile 준비 상태 불일치")
            runtime.require(len(metadata["accounts"]) == 1, "복제 계정 Host 구성 변경")
            account = metadata["accounts"][0]
            runtime.require(account["host"] == permissions.HOST and account["ssl_requirement"] == "ANY",
                            "복제 계정 Host/TLS 불일치")
            cursor.execute("SELECT @@GLOBAL.partial_revokes")
            partial = cursor.fetchone()[0]
            actual = permissions.grant_map(account["direct_grants"])
            expected = {"*.*": {"USAGE"}, "`awsdms_control`.*": {"ALL PRIVILEGES"},
                        "`seonjalal_develop`.*": permissions.PRIVILEGES,
                        permissions.scope(access.TARGET_SCHEMA, partial): permissions.PRIVILEGES,
                        permissions.scope(access.CONTROL_SCHEMA, partial): {"ALL PRIVILEGES"}}
            runtime.require(actual == expected, "계획과 다른 복제 계정 권한")
            cursor.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=%s",
                           (access.CONTROL_SCHEMA,))
            runtime.require(cursor.fetchone() == (0,), "새 제어 DB가 비어있지 않음")
    return {"source_ssl": source["ssl_mode"], "target_ssl": target["ssl_mode"],
            "replication_instance_class": instances[0]["ReplicationInstanceClass"],
            "replication_engine_version": instances[0]["EngineVersion"]}


def create_task(directory, body):
    record = directory / "cutover-dms-create-record.json"
    runtime.require(not record.exists(), "이전 생성 요청 기록 존재: 재실행 금지")
    state = {"task_name": TASK, "phase": "create_requested", "request_sha256": digest(body)}
    runtime.secure_write(record, state)
    print("DMS_TASK_CREATE_REQUESTED", flush=True)
    # API 응답 유실 시 이미 생성됐을 수 있으므로 intent 기록을 남기고 자동 재시도하지 않음.
    row = runtime.aws("dms", "create-replication-task", body=body)["ReplicationTask"]
    runtime.require(row["ReplicationTaskIdentifier"] == TASK and row["MigrationType"] == "full-load-and-cdc"
                    and row["SourceEndpointArn"] == access.SOURCE_ENDPOINT
                    and row["TargetEndpointArn"] == access.TARGET_ENDPOINT
                    and row["ReplicationInstanceArn"] == access.INSTANCE
                    and row["Status"] in ("creating", "ready"), "생성 결과 예상 밖 상태: 실제 Task 조회 필요")
    state.update({"phase": "created_not_started", "task_arn": row["ReplicationTaskArn"], "status": row["Status"]})
    runtime.secure_write(record, state)
    print("DMS_TASK_CREATED_NOT_STARTED=" + TASK)
    print("TASK_ARN=" + row["ReplicationTaskArn"])
    print("TASK_STATUS=" + row["Status"])
    print("NO_ENDPOINT_MODIFICATION_NO_REPLICATION_START_NO_APP_CHANGE")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True)
    parser.add_argument("--local-port", type=int, default=13306)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--approved-dms-task-create", action="store_true")
    args = parser.parse_args()
    runtime.require(not args.apply or args.approved_dms_task_create, "DMS 작업 생성 승인 옵션 필요")
    runtime.require(1 <= args.local_port <= 65535, "포트 범위 오류")
    directory = runtime.protected_directory(args.directory)
    metadata = preflight(directory, args.local_port)
    body = request()
    plan_path = directory / "cutover-dms-task-plan.json"
    if args.apply:
        runtime.require(not plan_path.is_symlink(), "계획 파일 심볼릭 링크 금지")
        plan = json.loads(plan_path.read_text())
        runtime.require(plan["request"] == body and plan["request_sha256"] == digest(body),
                        "검토한 계획과 현재 생성 요청 불일치")
        create_task(directory, body)
    else:
        runtime.secure_write(plan_path, {"request": body, "request_sha256": digest(body)})
        print(json.dumps({"mode": "PLAN_ONLY", "new_task": TASK,
                          "migration_type": "full-load-and-cdc", "source_schema": "seonjalal_develop",
                          "target_schema": access.TARGET_SCHEMA, "selected_table_names": 16,
                          "target_table_prep": "DO_NOTHING", "control_schema": access.CONTROL_SCHEMA,
                          "validation_enabled": True, "strict_error_stop": True,
                          "create_endpoints": 0, "modify_existing_resources": False,
                          "starts_replication": False, "source_catalog_check_required_before_start": True,
                          "source_tls_exception_approval_required_before_start": True,
                          "request_sha256": digest(body), **metadata}, ensure_ascii=False, indent=2))
        print("DMS_TASK_PLAN_FILE=" + str(plan_path))
        print("DMS_TASK_PLAN_OK_NO_AWS_OR_DB_WRITE_LOCAL_FILE_ONLY")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        detail = str(error) if isinstance(error, runtime.SafetyStop) else type(error).__name__
        raise SystemExit("STOP: " + detail + "; 생성 요청 이후 오류면 Task 이름으로 확인·재실행 금지")
