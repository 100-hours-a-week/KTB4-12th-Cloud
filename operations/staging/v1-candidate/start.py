#!/usr/bin/env python3
"""승인된 벤치마크에서만 독립 RDS 후보 생성. 기존 컨테이너/프록시 변경 금지."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request

INSTANCE = "i-0c6f5ea09b5e20859"
IMAGE = "sha256:b93ca625008ea5eb57e600dc56e033a7ca45bce8842ac76b789eb0da6563e1f2"
NAME = "seonjalal-v1-rds-rehearsal-candidate"
NETWORK = "seonjalal-v1-rds-rehearsal"
TARGET = Path("/opt/seonjalal-v1-rds-rehearsal")
SECRET = "arn:aws:secretsmanager:ap-northeast-2:721744297924:secret:seonjalal/staging/backend/runtime-DFJ1cU"
DB_HOST = "seonjalal-v2-migration-rehearsal.cbqckkusw4ke.ap-northeast-2.rds.amazonaws.com"


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def call(arguments):
    result = subprocess.run(arguments, capture_output=True, text=True, timeout=45)
    # Docker/AWS 상세 오류는 Secret을 포함할 수 있어 원문 출력 금지.
    require(result.returncode == 0, "SUBPROCESS_FAILED_" + arguments[0])
    return result.stdout


def aws(*arguments):
    return json.loads(call(["aws", *arguments, "--region", "ap-northeast-2",
                           "--no-cli-pager", "--output", "json",
                           "--cli-connect-timeout", "5", "--cli-read-timeout", "15"]))


def get(url):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=5) as response:
        require(response.status == 200, "HTTP_NOT_200")
        return response.read()


def identity_guard():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request("http://169.254.169.254/latest/api/token", method="PUT",
        headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"})
    with opener.open(request, timeout=3) as response:
        token = response.read().decode()
    request = urllib.request.Request("http://169.254.169.254/latest/meta-data/instance-id",
        headers={"X-aws-ec2-metadata-token": token})
    with opener.open(request, timeout=3) as response:
        require(response.read().decode() == INSTANCE, "WRONG_INSTANCE")
    identity = aws("sts", "get-caller-identity")
    require(identity["Arn"].startswith(
        "arn:aws:sts::721744297924:assumed-role/seonjalal-v1-develop-role/"), "WRONG_ROLE")


def main():
    os.umask(0o077)
    identity_guard()
    available = next(int(line.split()[1]) for line in Path("/proc/meminfo").read_text().splitlines()
                     if line.startswith("MemAvailable:"))
    require(available >= 1800 * 1024, "INSUFFICIENT_AVAILABLE_MEMORY")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 18083))
    source = json.loads(call(["docker", "inspect", "seonjalal-backend"]))[0]
    require(source["Image"] == IMAGE and source["State"]["Running"], "SOURCE_IMAGE_OR_STATE_CHANGED")
    source_env = dict(item.split("=", 1) for item in source["Config"]["Env"] if "=" in item)
    require(source_env.get("DB_URL", "").startswith("jdbc:mysql://database:"), "SOURCE_DB_CHANGED")
    require(not call(["docker", "ps", "-aq", "--filter", "name=^/" + NAME + "$"]).strip(),
            "CANDIDATE_ALREADY_EXISTS")
    require(not call(["docker", "network", "ls", "-q", "--filter", "name=^" + NETWORK + "$"]).strip(),
            "CANDIDATE_NETWORK_ALREADY_EXISTS")
    get("http://127.0.0.1:8081/api/actuator/health")
    secret = aws("secretsmanager", "get-secret-value", "--secret-id", SECRET,
                 "--version-stage", "AWSCURRENT")
    payload = json.loads(secret["SecretString"])
    keys = {"DB_PASSWORD", "JWT_SECRET_BASE64", "LOGIN_RATE_LIMIT_HMAC_SECRET_BASE64", "PROFILING_SERVICE_TOKEN"}
    require(set(payload) == keys and all(isinstance(v, str) and v and "\n" not in v and "\r" not in v
                                       for v in payload.values()), "SECRET_SHAPE_INVALID")
    environment = {
        "DB_URL": "jdbc:mysql://" + DB_HOST + ":3306/seonjalal_develop?sslMode=VERIFY_IDENTITY&fallbackToSystemTrustStore=true&connectTimeout=10000&socketTimeout=20000",
        "DB_USERNAME": "seonjalal_staging_app", "JWT_ISSUER": "seonjalal-staging",
        "AWS_REGION": "ap-northeast-2",
        "PRODUCT_IMAGE_BUCKET": "gift-dev-product-images-721744297924-ap-northeast-2-an",
        "CORS_ALLOWED_ORIGINS": "https://v2-staging.seonjalal.com,https://seonjalal-v2-develop-runtime.tail234485.ts.net:8443",
        "AI_PROFILE_BASE_URL": "http://127.0.0.1:9",
        "SPRING_FLYWAY_ENABLED": "false", "SPRING_SQL_INIT_MODE": "never",
        "SPRING_JPA_HIBERNATE_DDL_AUTO": "validate", "SCHEDULING_ENABLED": "false",
        "AI_PROFILE_SCHEDULING_ENABLED": "false", "SERVER_SHUTDOWN": "graceful",
        "SPRING_LIFECYCLE_TIMEOUT_PER_SHUTDOWN_PHASE": "45s",
        "JAVA_TOOL_OPTIONS": "-Xms128m -Xmx512m",
        **payload,
    }
    env_file = TARGET / "candidate.env"
    descriptor = os.open(env_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        stream.write("".join(key + "=" + value + "\n" for key, value in environment.items()))
    created = False
    try:
        call(["docker", "network", "create", "--label", "purpose=isolated-v1-rds-rehearsal", NETWORK])
        call(["docker", "create", "--name", NAME, "--pull=never", "--network", NETWORK,
              "--restart", "no", "--memory", "1024m", "--memory-swap", "1024m", "--cpus", "1",
              "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
              "--tmpfs", "/tmp:rw,nosuid,nodev,size=128m,mode=1777",
              "--publish", "127.0.0.1:18083:8080", "--env-file", str(env_file),
              "--mount", "type=bind,source=" + str(TARGET / "tls-bootstrap.sh") + ",target=/verification/tls-bootstrap.sh,readonly",
              "--mount", "type=bind,source=" + str(TARGET / "rds-ca.pem") + ",target=/verification/rds-ca.pem,readonly",
              "--log-opt", "max-size=10m", "--log-opt", "max-file=2",
              "--stop-timeout", "60", "--health-cmd", "curl -fsS http://127.0.0.1:8080/actuator/health >/dev/null || exit 1",
              "--health-interval", "10s", "--health-timeout", "5s", "--health-retries", "3",
              "--health-start-period", "120s", "--entrypoint", "/bin/sh", IMAGE, "-c",
              'export STAGING_RDS_CA_BUNDLE="$(cat /verification/rds-ca.pem)"; exec /bin/sh /verification/tls-bootstrap.sh'])
        created = True
        # 환경변수는 Docker 설정에 남으므로 Docker/root 권한자는 신뢰 경계에 포함.
        env_file.unlink()
        call(["docker", "start", NAME])
        print("CANDIDATE_STARTED_WAITING_FOR_HEALTH", flush=True)
        for attempt in range(60):
            state = json.loads(call(["docker", "inspect", NAME]))[0]["State"]
            require(state["Running"] and not state.get("OOMKilled"), "CANDIDATE_EXITED_OR_OOM")
            if state.get("Health", {}).get("Status") == "healthy":
                break
            time.sleep(3)
        else:
            raise RuntimeError("CANDIDATE_STARTUP_TIMEOUT")
        require(json.loads(get("http://127.0.0.1:18083/actuator/health"))["status"] == "UP", "CANDIDATE_NOT_UP")
        get("http://127.0.0.1:18083/products?sort=POPULAR")
        require(json.loads(call(["docker", "inspect", "seonjalal-backend"]))[0]["Id"] == source["Id"], "SOURCE_REPLACED")
        get("http://127.0.0.1:8081/api/actuator/health")
        (TARGET / "verification.json").write_text(json.dumps({
            "instance": INSTANCE, "image": IMAGE, "secret_version_id": secret["VersionId"],
            "candidate": NAME, "health": "UP", "products_http": 200,
            "source_container_id": source["Id"], "business_writes_executed": False,
        }, indent=2))
        print("CANDIDATE_HEALTH_AND_PRODUCTS_HTTP=200")
        print("EXISTING_V1_HEALTH_HTTP=200")
        print("CANDIDATE_READY_NO_TRAFFIC_SWITCH_OR_BUSINESS_WRITE")
    except Exception:
        if created:
            subprocess.run(["docker", "stop", "--time", "60", NAME], capture_output=True, timeout=75)
        raise
    finally:
        if env_file.exists():
            env_file.unlink()


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # 원문 예외·Docker 로그에는 비밀이 포함될 수 있어 출력하지 않음.
        message = str(error) if type(error) is RuntimeError else type(error).__name__
        print("STOP_CANDIDATE=" + message, file=sys.stderr)
        sys.exit(1)
