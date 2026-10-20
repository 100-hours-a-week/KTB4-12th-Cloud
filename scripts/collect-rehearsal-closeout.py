#!/usr/bin/env python3
"""Mac: 정리 전 결과 보존·운영 DB 의존성·ECS 제어 정보 읽기. 삭제/중지 없음."""
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
SOURCE = "i-0c6f5ea09b5e20859"
PRODUCTION = "i-0417358a95b551c12"
CLUSTER = "seonjalal-v2-staging-cluster"
WORK = "/opt/seonjalal-cutover-run-20261019-yzhs0ftt"
FILES = ("events.jsonl", "result.json", "read-probes.json", "original.conf")


def require(value, message):
    if not value:
        raise RuntimeError(message)


def aws(service, operation, *args):
    try:
        result = subprocess.run(["aws", "--profile", "seonjalal-production", "--region", "ap-northeast-2",
            "--no-cli-pager", "--no-cli-auto-prompt", "--output", "json", "--cli-connect-timeout", "3",
            "--cli-read-timeout", "15", service, operation, *args], capture_output=True, text=True,
            timeout=25, env=dict(os.environ, AWS_MAX_ATTEMPTS="1"))
    except subprocess.TimeoutExpired:
        raise RuntimeError(operation + "_TIMEOUT") from None
    if result.returncode:
        match = re.search(r"An error occurred \(([A-Za-z0-9]+)\)", result.stderr)
        raise RuntimeError(operation + "_" + (match.group(1) if match else "CLI_FAILED"))
    return json.loads(result.stdout) if result.stdout.strip() else {}


def save(directory, name, value):
    with (directory / name).open("x") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.write("\n")


def remote(instance, code):
    # 두 고정 호스트에서 검토한 읽기 코드만 실행. 자원/DB/서비스 변경 명령 없음.
    require(instance in (SOURCE, PRODUCTION), "INSTANCE_NOT_ALLOWED")
    command = aws("ssm", "send-command", "--document-name", "AWS-RunShellScript",
        "--instance-ids", instance, "--comment", "Read-only rehearsal closeout evidence and DB host inspection",
        "--parameters", json.dumps({"commands": ["python3 -c " + shlex.quote(code)], "executionTimeout": ["90"]}))
    command_id = command["Command"]["CommandId"]
    print("READONLY_COMMAND_ID=" + command_id, flush=True)
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        try:
            row = aws("ssm", "get-command-invocation", "--command-id", command_id, "--instance-id", instance)
        except RuntimeError as error:
            if "InvocationDoesNotExist" not in str(error):
                raise
            time.sleep(2)
            continue
        if row["Status"] == "Success":
            require(row["ResponseCode"] == 0, "READONLY_COMMAND_EXIT_FAILED")
            return json.loads(row["StandardOutputContent"])
        require(row["Status"] in ("Pending", "InProgress", "Delayed"), "READONLY_COMMAND_" + row["Status"])
        time.sleep(2)
    raise RuntimeError("READONLY_RESULT_TIMEOUT")


def evidence_program(expected_hash):
    return '''import base64,hashlib,json,os,stat
from pathlib import Path
root=Path(__WORK__)
if root.is_symlink() or not stat.S_ISDIR(root.lstat().st_mode): raise RuntimeError('WRONG_RESULT_DIRECTORY')
files={}
for name in __FILES__:
    path=root/name
    info=path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid!=0 or info.st_size>12000: raise RuntimeError('INVALID_ARTIFACT')
    value=path.read_bytes()
    if name=='original.conf' and hashlib.sha256(value).hexdigest()!=__HASH__: raise RuntimeError('ORIGINAL_CONFIG_CHANGED')
    files[name]={'base64':base64.b64encode(value).decode(),'sha256':hashlib.sha256(value).hexdigest()}
result=json.dumps({'files':files})
if len(result.encode())>=22000: raise RuntimeError('OUTPUT_LIMIT')
print(result)
'''.replace("__WORK__", repr(WORK)).replace("__FILES__", repr(FILES)).replace("__HASH__", repr(expected_hash))


def production_program():
    # Docker 환경 전체는 메모리에서만 읽고, URL은 host/schema만 내보냄.
    return '''import json,re,subprocess
from urllib.parse import urlsplit
def run(args):
    result=subprocess.run(args,capture_output=True,text=True,timeout=20)
    if result.returncode: raise RuntimeError('DOCKER_READ_FAILED')
    return result.stdout
names=run(['docker','ps','--format','{{.Names}}']).splitlines()
if not names: raise RuntimeError('NO_RUNNING_CONTAINERS')
items=json.loads(run(['docker','inspect',*names]))
rows=[]
for item in items:
    connections=[]
    for value in item['Config'].get('Env',[]):
        key,sep,value=value.partition('=')
        if not sep: continue
        if key in ('DB_URL','DATABASE_URL','SPRING_DATASOURCE_URL') or key.endswith(('_DATABASE_URL','_DB_URL')):
            parsed=urlsplit(value.removeprefix('jdbc:'))
            if parsed.hostname:
                schema=parsed.path.lstrip('/')
                connections.append({'key':key,'host':parsed.hostname,'schema':schema if re.fullmatch(r'[A-Za-z0-9_]+',schema) else '[not displayed]'})
        elif key in ('DB_HOST','DATABASE_HOST','MYSQL_HOST','RDS_HOST') and re.fullmatch(r'[A-Za-z0-9.-]+',value):
            connections.append({'key':key,'host':value})
    rows.append({'container':item['Name'],'db_connections':connections})
print(json.dumps({'production_instance':'i-0417358a95b551c12','containers':rows,'limitation':'Detected environment URL/host fields only; aliases and external clients require review'}))
'''


def write_artifacts(directory, bundle):
    require(set(bundle["files"]) == set(FILES), "ARTIFACT_LIST_MISMATCH")
    for name in FILES:
        info = bundle["files"][name]
        value = base64.b64decode(info["base64"], validate=True)
        require(hashlib.sha256(value).hexdigest() == info["sha256"], "ARTIFACT_HASH_MISMATCH")
        with (directory / name).open("xb") as stream:
            stream.write(value)
    save(directory, "artifact-hashes.json", {k: v["sha256"] for k, v in bundle["files"].items()})


def main():
    require(aws("sts", "get-caller-identity")["Account"] == "721744297924", "WRONG_ACCOUNT")
    os.umask(0o077)
    parent = ROOT / "docs/rehearsals"
    directory = Path(tempfile.mkdtemp(prefix="closeout-20261019-", dir=parent))
    print("LOCAL_EVIDENCE_DIR=" + str(directory), flush=True)
    original_hash = hashlib.sha256((ROOT / "nginx/conf.d/default.conf").read_bytes()).hexdigest()
    write_artifacts(directory, remote(SOURCE, evidence_program(original_hash)))
    print("CUTOVER_ARTIFACTS_SAVED_AND_HASH_VERIFIED=4", flush=True)
    dependencies = remote(PRODUCTION, production_program())
    save(directory, "v1-production-db-dependencies.json", dependencies)
    print("V1_PRODUCTION_DB_DEPENDENCIES=" + json.dumps(dependencies, ensure_ascii=False), flush=True)
    clusters = aws("ecs", "describe-clusters", "--clusters", CLUSTER)
    require(not clusters.get("failures") and len(clusters["clusters"]) == 1, "STAGING_CLUSTER_NOT_FOUND")
    cluster = clusters["clusters"][0]
    service_arns = aws("ecs", "list-services", "--cluster", CLUSTER)["serviceArns"]
    services = aws("ecs", "describe-services", "--cluster", CLUSTER, "--services", *service_arns) if service_arns else {"services": []}
    providers = cluster.get("capacityProviders", [])
    provider_rows = aws("ecs", "describe-capacity-providers", "--capacity-providers", *providers) if providers else {"capacityProviders": []}
    ecs = {"cluster": CLUSTER, "services": [
        {k: row.get(k) for k in ("serviceName", "serviceArn", "desiredCount", "runningCount", "pendingCount", "schedulingStrategy")}
        for row in services["services"]], "capacityProviders": provider_rows["capacityProviders"],
        "runningTaskArns": aws("ecs", "list-tasks", "--cluster", CLUSTER, "--desired-status", "RUNNING")["taskArns"]}
    save(directory, "ecs-shutdown-input.json", ecs)
    print("ECS_SHUTDOWN_INPUT=" + json.dumps(ecs), flush=True)
    tasks = aws("dms", "describe-replication-tasks", "--without-settings")["ReplicationTasks"]
    saved = []
    for row in tasks:
        if row["ReplicationInstanceArn"] != "arn:aws:dms:ap-northeast-2:721744297924:rep:RHRA7UPZEBCZLEYTA6WIJUTU2Q":
            continue
        task = {k: row.get(k) for k in ("ReplicationTaskIdentifier", "ReplicationTaskArn", "Status", "ReplicationTaskStats",
                                       "SourceEndpointArn", "TargetEndpointArn", "TableMappings")}
        task["tables"] = aws("dms", "describe-table-statistics", "--replication-task-arn", row["ReplicationTaskArn"])["TableStatistics"]
        saved.append(task)
    save(directory, "dms-before-cleanup.json", saved)
    print("DMS_TASK_EVIDENCE_SAVED=" + str(len(saved)))
    print("CLOSEOUT_READONLY_COMPLETE_NO_INSTANCE_DB_OR_ASG_CHANGE")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        detail = str(error) if type(error) is RuntimeError else type(error).__name__
        raise SystemExit("STOP_CLOSEOUT_READ=" + detail + "; preserve collected files; no delete/stop performed")
