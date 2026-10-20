#!/usr/bin/env python3
"""승인된 Develop 풀 진단용 SSM 요청 생성. 이 도구 자체는 AWS 호출 없음."""
import argparse
import json
import os
from pathlib import Path
import tempfile

REMOTE = r'''
import datetime
import fcntl
import json
import os
from pathlib import Path
import subprocess
import tempfile
import urllib.request

MAIN = "seonjalal-v2-develop-main-backend-1"
FE = "seonjalal-v2-develop-frontend-1"
DB = "seonjalal-v2-develop-database-1"
PAY = "seonjalal-v2-develop-payment-server-1"
IMAGE = "sha256:9484e5ce77fe6748d784ef623d366a7b2d700b3f115bf94c2999f15f42bb3a70"
KEY = "SPRING_DATASOURCE_HIKARI_LEAK_DETECTION_THRESHOLD"
BASE = Path("/opt/seonjalal-develop")

def run(args, env=None):
    result = subprocess.run(args, env=env, capture_output=True, text=True)
    if result.returncode:
        # Docker 출력에는 설정 값이 포함될 수 있으므로 공유 출력에 포함하지 않음.
        raise RuntimeError("COMMAND_FAILED=" + args[0] + ":" + str(result.returncode))
    return result.stdout

def inspect(name):
    return json.loads(run(["docker", "inspect", name]))[0]

def envmap(items):
    return dict(x.split("=", 1) for x in (items or []) if "=" in x)

def write_private(path, content):
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(content)

def identity():
    # 다른 서버에서 실행되는 일을 막기 위해 IMDSv2 instance-id 확인.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    token_request = urllib.request.Request(
        "http://169.254.169.254/latest/api/token", method="PUT",
        headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"})
    with opener.open(token_request, timeout=3) as response:
        token = response.read().decode()
    request = urllib.request.Request(
        "http://169.254.169.254/latest/meta-data/instance-id",
        headers={"X-aws-ec2-metadata-token": token})
    with opener.open(request, timeout=3) as response:
        if response.read().decode() != "i-06f8ccdce3a1e9ff4":
            raise RuntimeError("INSTANCE_MISMATCH")

def inventory():
    return dict(line.split(" ", 1) for line in run(
        ["docker", "ps", "-a", "--format", "{{.Names}} {{.ID}}"]
    ).splitlines())

def main():
    identity()
    with open("/run/seonjalal-develop-pool-diagnostic.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        perform()

def perform():
    marker = BASE / "pool-diagnostic-active.json"
    if marker.exists():
        raise RuntimeError("DIAGNOSTIC_ALREADY_PREPARED_DO_NOT_REPEAT")
    old, frontend, database, payment = [inspect(n) for n in (MAIN, FE, DB, PAY)]
    labels = old["Config"].get("Labels") or {}
    if (old["Image"] != IMAGE or not old["State"]["Running"]
        or labels.get("com.docker.compose.project") != "seonjalal-v2-develop"
        or labels.get("com.docker.compose.project.config_files") != str(BASE / "compose.yaml")):
        raise RuntimeError("CURRENT_MAIN_IDENTITY_MISMATCH")
    original_env = envmap(old["Config"].get("Env"))
    if KEY in original_env:
        raise RuntimeError("LEAK_SETTING_ALREADY_EXISTS")

    # 실행 중인 컨테이너 값으로 Secret 재조회 및 값 변경 방지.
    render_env = dict(os.environ)
    render_env.update(original_env)
    db_env = envmap(database["Config"].get("Env"))
    render_env.update({
        "DB_NAME": db_env["MYSQL_DATABASE"], "DB_USER": db_env["MYSQL_USER"],
        "DB_ROOT_PASSWORD": db_env["MYSQL_ROOT_PASSWORD"],
        "MAIN_BACKEND_IMAGE": old["Config"]["Image"],
        "FRONTEND_IMAGE": frontend["Config"]["Image"],
        "PAYMENT_SERVER_IMAGE": payment["Config"]["Image"],
    })
    base_cmd = ["docker", "compose", "--project-directory", str(BASE),
                "--env-file", str(BASE / ".env"), "-p", "seonjalal-v2-develop",
                "-f", str(BASE / "compose.yaml")]
    config = json.loads(run(base_cmd + ["config", "--format", "json"], render_env))
    image_env = envmap(json.loads(run(["docker", "image", "inspect", IMAGE]))[0]["Config"].get("Env"))
    effective = dict(image_env)
    effective.update(config["services"]["main-backend"].get("environment", {}))
    if effective != original_env:
        raise RuntimeError("RENDERED_MAIN_ENV_DIFFERS_FROM_RUNNING_CONTAINER")
    expected_hash = labels.get("com.docker.compose.config-hash")
    hash_output = run(base_cmd + ["config", "--hash", "main-backend"], render_env)
    if not expected_hash or hash_output.split()[-1] != expected_hash:
        raise RuntimeError("COMPOSE_CONFIG_CHANGED_SINCE_DEPLOYMENT")

    before = inventory()
    directory = Path(tempfile.mkdtemp(prefix="pool-diagnostic-", dir=BASE))
    os.chmod(directory, 0o700)
    write_private(directory / "main-before-inspect.json", json.dumps(old))
    # 재생성 전 같은 이미지 ID와 환경변수로 돌아갈 설정 보관.
    service = config["services"]["main-backend"]
    service["image"] = IMAGE
    write_private(directory / "rollback.compose.json", json.dumps(config))
    service["environment"][KEY] = "20000"
    write_private(directory / "diagnostic.compose.json", json.dumps(config))
    write_private(marker, json.dumps({"directory": str(directory), "main_image": IMAGE}))
    print("PRIVATE_BACKUP_DIR=" + str(directory), flush=True)
    print("DO_NOT_SHARE_BACKUP_CONTENTS", flush=True)

    # 부분 실패 시 증거를 보존하고 중단. DB·Payment·FE 재생성 금지.
    command = ["docker", "compose", "--project-directory", str(BASE),
               "-p", "seonjalal-v2-develop", "-f", str(directory / "diagnostic.compose.json")]
    started = datetime.datetime.now(datetime.timezone.utc).isoformat()
    write_private(directory / "started-at.txt", started)
    run(command + ["up", "--detach", "--no-deps", "--pull", "never",
                   "--no-build", "--force-recreate", "--wait", "--wait-timeout", "180",
                   "main-backend"])
    current = inspect(MAIN)
    new_env = envmap(current["Config"].get("Env"))
    expected_env = dict(original_env, **{KEY: "20000"})
    if current["Image"] != IMAGE or new_env != expected_env:
        raise RuntimeError("POST_RECREATE_IMAGE_OR_ENV_MISMATCH")
    after = inventory()
    if {k:v for k,v in before.items() if k != MAIN} != {k:v for k,v in after.items() if k != MAIN}:
        raise RuntimeError("OTHER_CONTAINER_INVENTORY_CHANGED")
    run(["docker", "exec", FE, "nginx", "-t"])
    run(["docker", "exec", FE, "nginx", "-s", "reload"])
    run(["curl", "--fail", "--silent", "--show-error", "--connect-timeout", "3",
         "--max-time", "12", "--output", "/dev/null",
         "http://127.0.0.1:8080/api/actuator/health"])
    print("MAIN_SAME_IMAGE_LEAK_DETECTION_20000_VERIFIED")
    print("OTHER_CONTAINER_IDS_PRESERVED_FE_NGINX_RELOADED")
    print("LOCAL_PROXY_HEALTH_HTTP_200")
    print("LIMITATION=Temporary recovery only; leak origin and SSE QA pending")

try:
    main()
except Exception as error:
    print("STOP=" + (str(error) if isinstance(error, RuntimeError) else type(error).__name__))
    print("DO_NOT_REPEAT_OR_RUN_FULL_DEPLOY; preserve backups and inspect partial state")
    raise SystemExit(1)
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approved-develop-diagnostic", action="store_true", required=True)
    parser.parse_args()
    compile(REMOTE, "<remote>", "exec")
    directory = Path(tempfile.mkdtemp(prefix="seonjalal-develop-pool-diagnostic-"))
    os.chmod(directory, 0o700)
    request = {
        "DocumentName": "AWS-RunShellScript",
        "InstanceIds": ["i-06f8ccdce3a1e9ff4"],
        "Comment": "Approved Develop same-image main-only Hikari diagnostic",
        "TimeoutSeconds": 600,
        "Parameters": {"commands": ["python3 - <<'PY'\n" + REMOTE + "\nPY"],
                       "executionTimeout": ["480"]},
        "MaxConcurrency": "1", "MaxErrors": "0",
    }
    path = directory / "request.json"
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(request, stream)
    print("REQUEST_FILE=" + str(path))
    print("LOCAL_REQUEST_ONLY_NO_AWS_CALL")


if __name__ == "__main__":
    main()
