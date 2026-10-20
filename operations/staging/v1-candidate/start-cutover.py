#!/usr/bin/env python3
"""고정 비운영 호스트에서 새 schema용 후보만 추가. 기존 앱/라우팅/업무쓰기 변경 없음."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request
from urllib.parse import urlsplit
import uuid

INSTANCE = "i-0c6f5ea09b5e20859"
IMAGE = "sha256:b93ca625008ea5eb57e600dc56e033a7ca45bce8842ac76b789eb0da6563e1f2"
SOURCE = "seonjalal-backend"
OLD = "seonjalal-v1-rds-rehearsal-candidate"
NAME = "seonjalal-v1-cutover-20261019-candidate"
NETWORK = "seonjalal-cutover-20261019"
TARGET = Path("/opt/seonjalal-cutover-20261019-candidate")
PORT = 18085
DB_HOST = "seonjalal-v2-migration-rehearsal.cbqckkusw4ke.ap-northeast-2.rds.amazonaws.com"
SCHEMA = "seonjalal_cutover_20261019"
KEEP = [SOURCE, OLD, "seonjalal-frontend", "seonjalal-database"]
SAFE_FLAGS = {"SPRING_FLYWAY_ENABLED": "false", "SPRING_SQL_INIT_MODE": "never",
              "SPRING_JPA_HIBERNATE_DDL_AUTO": "validate", "SCHEDULING_ENABLED": "false",
              "AI_PROFILE_SCHEDULING_ENABLED": "false"}
AUTH = ["JWT_SECRET_BASE64", "JWT_ISSUER", "LOGIN_RATE_LIMIT_HMAC_SECRET_BASE64"]


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def call(arguments, *, input=None, timeout=45):
    result = subprocess.run(arguments, input=input, capture_output=True, text=True, timeout=timeout)
    # CLI/Docker 오류 원문과 환경변수는 출력하지 않음.
    require(result.returncode == 0, "COMMAND_FAILED_" + arguments[0])
    return result.stdout.strip()


def get(url):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=5) as response:
        require(response.status == 200, "HTTP_NOT_200")
        return response.read(1048576)


def identity_guard():
    require(os.getuid() == 0, "ROOT_REQUIRED")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request("http://169.254.169.254/latest/api/token", method="PUT",
        headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"})
    with opener.open(request, timeout=3) as response:
        token = response.read().decode()
    request = urllib.request.Request("http://169.254.169.254/latest/meta-data/instance-id",
        headers={"X-aws-ec2-metadata-token": token})
    with opener.open(request, timeout=3) as response:
        require(response.read().decode() == INSTANCE, "WRONG_INSTANCE")
    identity = json.loads(call(["aws", "sts", "get-caller-identity", "--region", "ap-northeast-2",
                                "--no-cli-pager", "--output", "json"]))
    require(identity["Account"] == "721744297924" and identity["Arn"].startswith(
        "arn:aws:sts::721744297924:assumed-role/seonjalal-v1-develop-role/"), "WRONG_AWS_ROLE")


def env(item):
    return dict(value.split("=", 1) for value in item["Config"]["Env"] if "=" in value)


def memory_available():
    return next(int(line.split()[1]) // 1024 for line in Path("/proc/meminfo").read_text().splitlines()
                if line.startswith("MemAvailable:"))


def build_environment(source, old):
    require(source["Image"] == IMAGE and source["State"]["Running"], "SOURCE_IMAGE_OR_STATE_CHANGED")
    require(old["Image"] == IMAGE and old["State"]["Running"], "OLD_CANDIDATE_IMAGE_OR_STATE_CHANGED")
    source_env, old_env = env(source), env(old)
    url = urlsplit(source_env.get("DB_URL", "").removeprefix("jdbc:"))
    require(url.hostname == "database" and url.path == "/seonjalal_develop", "SOURCE_DB_CHANGED")
    url = urlsplit(old_env.get("DB_URL", "").removeprefix("jdbc:"))
    require(url.hostname == DB_HOST and url.path == "/seonjalal_develop", "OLD_CANDIDATE_DB_CHANGED")
    require(old_env.get("DB_USERNAME") == "seonjalal_staging_app"
            and bool(old_env.get("DB_PASSWORD")), "OLD_CANDIDATE_DB_ACCOUNT_CHANGED")
    require(all(source_env.get(key) == value for key, value in SAFE_FLAGS.items()), "SOURCE_INIT_OR_SCHEDULER_CHANGED")
    require(all(source_env.get(key) for key in AUTH), "SOURCE_AUTH_SETTING_MISSING")
    # Source 인증 계약 전체를 유지. 기존 후보의 독립 JWT/issuer로 덮어쓰지 않음.
    result = dict(source_env)
    for key in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        require(not result.get(key), "STATIC_AWS_CREDENTIALS_NOT_ALLOWED")
        result.pop(key, None)
    # 다른 datasource 환경변수가 DB_URL보다 우선해 기존 DB로 연결되는 경우 차단.
    jdbc = ("jdbc:mysql://" + DB_HOST + ":3306/" + SCHEMA +
            "?sslMode=VERIFY_IDENTITY&fallbackToSystemTrustStore=true&connectTimeout=10000&socketTimeout=20000")
    result.update({"DB_URL": jdbc, "DB_USERNAME": old_env["DB_USERNAME"], "DB_PASSWORD": old_env["DB_PASSWORD"],
                   "SPRING_DATASOURCE_URL": jdbc, "SPRING_DATASOURCE_USERNAME": old_env["DB_USERNAME"],
                   "SPRING_DATASOURCE_PASSWORD": old_env["DB_PASSWORD"], **SAFE_FLAGS,
                   "JAVA_TOOL_OPTIONS": "-Xms128m -Xmx512m", "SERVER_SHUTDOWN": "graceful",
                   "SPRING_LIFECYCLE_TIMEOUT_PER_SHUTDOWN_PHASE": "45s"})
    for key in ("STAGING_TLS_PREPARE_ONLY", "STAGING_RDS_CA_FILE", "STAGING_RDS_CA_BUNDLE"):
        result.pop(key, None)
    require(all("\n" not in value and "\r" not in value and "\x00" not in value for value in result.values()),
            "MULTILINE_ENV_NOT_SUPPORTED")
    return result


def write_record(state):
    temporary = TARGET / ("verification-" + uuid.uuid4().hex + ".json")
    with temporary.open("x") as stream:
        json.dump(state, stream)
    temporary.chmod(0o600)
    temporary.replace(TARGET / "verification.json")


def main(files):
    os.umask(0o077)
    identity_guard()
    require(not TARGET.exists() and not TARGET.is_symlink(), "EXISTING_CANDIDATE_DIRECTORY_PRESERVED")
    require(memory_available() >= 1800, "MEMORY_AVAILABLE_BELOW_1800_MIB")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", PORT))
    require(not call(["docker", "ps", "-aq", "--filter", "name=^/" + NAME + "$"]), "CANDIDATE_ALREADY_EXISTS")
    require(not call(["docker", "network", "ls", "-q", "--filter", "name=^" + NETWORK + "$"]),
            "CANDIDATE_NETWORK_ALREADY_EXISTS")
    items = json.loads(call(["docker", "inspect", *KEEP]))
    by_name = {item["Name"].lstrip("/"): item for item in items}
    require(set(by_name) == set(KEEP) and all(item["State"]["Running"] for item in items), "EXISTING_SERVICE_NOT_RUNNING")
    environment = build_environment(by_name[SOURCE], by_name[OLD])
    require(json.loads(get("http://127.0.0.1:8081/api/actuator/health"))["status"] == "UP", "SOURCE_NOT_UP")
    require(set(files) == {"start.py", "tls-bootstrap.sh", "rds-ca.pem"}, "UNEXPECTED_PUBLIC_FILES")
    TARGET.mkdir(mode=0o700)  # 중복 요청도 최초 mkdir만 통과. 기존 디렉터리 덮어쓰기 없음.
    for name, content in files.items():
        with (TARGET / name).open("xb") as stream:
            stream.write(content)
        (TARGET / name).chmod(0o600 if name == "start.py" else 0o644)
    state = {"phase": "prepared", "instance": INSTANCE, "image": IMAGE, "candidate": NAME,
             "schema": SCHEMA, "port": PORT, "requested_at_utc": datetime.now(timezone.utc).isoformat(),
             "preserved_ids": {name: item["Id"] for name, item in by_name.items()}, "business_writes": False}
    write_record(state)
    created_id = None
    try:
        state["phase"] = "network_create_requested"
        write_record(state)
        call(["docker", "network", "create", "--label", "purpose=isolated-cutover-20261019", NETWORK])
        state["phase"] = "container_create_requested"
        write_record(state)
        command = ["docker", "create", "--name", NAME, "--pull=never", "--network", NETWORK,
                   "--label", "purpose=isolated-cutover-20261019", "--restart", "no",
                   "--memory", "1024m", "--memory-swap", "1024m", "--cpus", "1",
                   "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                   "--tmpfs", "/tmp:rw,nosuid,nodev,size=128m,mode=1777",
                   "--publish", "127.0.0.1:18085:8080", "--env-file", "/dev/stdin",
                   "--mount", "type=bind,source=" + str(TARGET / "tls-bootstrap.sh") + ",target=/verification/tls-bootstrap.sh,readonly",
                   "--mount", "type=bind,source=" + str(TARGET / "rds-ca.pem") + ",target=/verification/rds-ca.pem,readonly",
                   "--log-opt", "max-size=10m", "--log-opt", "max-file=2", "--stop-timeout", "60",
                   "--health-cmd", "curl -fsS http://127.0.0.1:8080/actuator/health >/dev/null || exit 1",
                   "--health-interval", "10s", "--health-timeout", "5s", "--health-retries", "3",
                   "--health-start-period", "120s", "--entrypoint", "/bin/sh", IMAGE, "-c",
                   'export STAGING_RDS_CA_BUNDLE="$(cat /verification/rds-ca.pem)"; exec /bin/sh /verification/tls-bootstrap.sh']
        # 비밀 환경값은 argv/SSM 요청/호스트 env파일에 기록하지 않고 stdin으로만 전달.
        created_id = call(command, input="".join(key + "=" + value + "\n" for key, value in environment.items()))
        require(bool(created_id), "CREATE_RESPONSE_ID_MISSING")
        state.update(phase="container_created", candidate_id=created_id)
        write_record(state)
        call(["docker", "start", NAME])
        print("NEW_CANDIDATE_STARTED_WAITING_FOR_HEALTH", flush=True)
        for attempt in range(60):
            require(memory_available() >= 512, "HOST_MEMORY_PRESSURE_STOP_NEW_CANDIDATE")
            current = json.loads(call(["docker", "inspect", NAME]))[0]
            require(current["Id"] == created_id and current["State"]["Running"]
                    and not current["State"].get("OOMKilled"), "NEW_CANDIDATE_EXITED_OR_OOM")
            if current["State"].get("Health", {}).get("Status") == "healthy":
                break
            time.sleep(3)
        else:
            raise RuntimeError("NEW_CANDIDATE_HEALTH_TIMEOUT")
        actual = env(current)
        require(all(actual.get(key) == environment[key] for key in AUTH), "NEW_CANDIDATE_AUTH_MISMATCH")
        require(actual["DB_URL"] == environment["DB_URL"] and current["Image"] == IMAGE,
                "NEW_CANDIDATE_IMAGE_OR_DB_MISMATCH")
        require(json.loads(get("http://127.0.0.1:18085/actuator/health"))["status"] == "UP", "CANDIDATE_NOT_UP")
        products = json.loads(get("http://127.0.0.1:18085/products?sort=POPULAR"))
        require(products.get("error") is None and isinstance(products.get("data", {}).get("products"), list),
                "CANDIDATE_PRODUCTS_INVALID")
        after = json.loads(call(["docker", "inspect", *KEEP]))
        require({item["Name"].lstrip("/"): item["Id"] for item in after} == state["preserved_ids"]
                and all(item["State"]["Running"] for item in after), "EXISTING_CONTAINERS_CHANGED")
        require(env(next(item for item in after if item["Name"] == "/" + SOURCE)) == env(by_name[SOURCE]),
                "SOURCE_ENV_CHANGED_DURING_START")
        require(json.loads(get("http://127.0.0.1:8081/api/actuator/health"))["status"] == "UP", "SOURCE_HEALTH_FAILED")
        state.update(phase="health_and_products_verified", verified_at_utc=datetime.now(timezone.utc).isoformat(),
                     authentication_config_matches_source=True, app_auth_request_verified=False)
        write_record(state)
        print("NEW_CANDIDATE_HEALTH_AND_PRODUCTS_HTTP=200")
        print("SAME_IMAGE_SOURCE_AUTH_CONFIG_AND_NEW_TARGET_VERIFIED")
        print("EXISTING_FOUR_CONTAINERS_PRESERVED_SOURCE_HEALTH_HTTP=200")
        print("CANDIDATE_URL=http://127.0.0.1:18085")
        print("NO_TRAFFIC_SWITCH_NO_BUSINESS_WRITE_NO_EXISTING_CONTAINER_RESTART")
        print("LIMITATION=Authentication request QA, DB orphan checks, cutover and rollback timing pending")
    except Exception:
        state["phase"] = "failed_preserved"
        if created_id:
            try:
                current = json.loads(call(["docker", "inspect", NAME]))[0]
                if current["Id"] == created_id and current["State"]["Running"]:
                    call(["docker", "stop", "--time", "30", NAME], timeout=45)
                state["new_candidate_stopped"] = current["Id"] == created_id
            except Exception:
                state["new_candidate_stop_requires_inspection"] = True
        write_record(state)
        raise


if __name__ == "__main__":
    print("STOP: Run only through the reviewed public-file SSM bootstrap", file=sys.stderr)
    sys.exit(1)
