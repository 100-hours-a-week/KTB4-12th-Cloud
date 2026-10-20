#!/usr/bin/env python3
"""Mac: 관측 노드 최초 설치용 SSM 요청 생성. AWS 호출·원격 실행 없음."""
import base64
import json
import os
from pathlib import Path
import tempfile

INSTANCE = "i-0c6a75f630436591c"
PROMETHEUS = "prom/prometheus:v3.15.0"
GRAFANA = "grafana/grafana:13.2.4"

REMOTE = r'''
import base64, fcntl, json, os, pathlib, secrets, shutil, subprocess, time, urllib.request

def require(ok, message):
    if not ok:
        raise SystemExit("STOP: " + message)

def docker(*args):
    result = subprocess.run(["docker", *args], capture_output=True, text=True)
    require(result.returncode == 0, "Docker 단계 실패: " + args[0] + "; 재실행/삭제 전 상태 확인 필요")
    return result.stdout.strip()

# 다른 EC2에서 실행되면 파일 생성·컨테이너 조작 전 중단.
require(os.geteuid() == 0, "root 필요")
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
token_request = urllib.request.Request("http://169.254.169.254/latest/api/token", method="PUT",
    headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"})
with opener.open(token_request, timeout=3) as response:
    token = response.read().decode()
identity_request = urllib.request.Request("http://169.254.169.254/latest/dynamic/instance-identity/document",
    headers={"X-aws-ec2-metadata-token": token})
with opener.open(identity_request, timeout=3) as response:
    identity = json.load(response)
require((identity.get("accountId"), identity.get("region"), identity.get("instanceId")) ==
    ("721744297924", "ap-northeast-2", "i-0c6a75f630436591c"), "관측 노드 대상 불일치")
# 사전검사보다 먼저 배타 잠금. 동시에 들어온 최초 설치 요청의 경합 방지.
# 기존에 이미 전송된 요청에는 소급 적용되지 않으므로 진단 전 재전송 금지.
lock_fd = os.open('/run/lock/seonjalal-observability-install.lock',
                  os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
try:
    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    raise SystemExit('STOP: 다른 관측 설치 실행 중. 재전송 금지')
require(shutil.disk_usage("/opt").free > 10 * 1024**3, "디스크 여유10GiB 필요")
root = pathlib.Path("/opt/seonjalal-observability")
require(not root.exists() and not root.is_symlink(), "기존 설치 경로 보존. 최초 설치만 허용")
names = ("seonjalal-monitor-prometheus", "seonjalal-monitor-grafana")
existing = set(docker("ps", "-a", "--format", "{{.Names}}").splitlines())
require(not existing, "관측 노드 기존 컨테이너 확인 필요")
for kind, name in (("network", "seonjalal-monitor"), ("volume", "seonjalal-monitor-prometheus-data"),
                   ("volume", "seonjalal-monitor-grafana-data")):
    result = subprocess.run(["docker", kind, "inspect", name], capture_output=True)
    require(result.returncode != 0, "기존 관측 network/volume 보존 필요")
docker("info")

# 먼저 두 이미지를 준비. 실패 시 설정/관리자 비밀번호/컨테이너는 생성하지 않음.
images = {}
for label, tag in (("prometheus", "__PROMETHEUS__"), ("grafana", "__GRAFANA__")):
    docker("pull", "--platform", "linux/amd64", tag)
    detail = json.loads(docker("image", "inspect", tag))[0]
    require(detail["Architecture"] == "amd64" and detail["Os"] == "linux", "이미지 플랫폼 불일치")
    require(bool(detail.get("RepoDigests")), "이미지 Digest 없음")
    images[label] = {"tag": tag, "digest": detail["RepoDigests"][0], "config": detail["Id"]}

os.umask(0o077)
root.mkdir(mode=0o700)
config = root / "config"
config.mkdir(mode=0o755)
os.chmod(config, 0o755)
provisioning = config / "provisioning"
provisioning.mkdir(mode=0o755)
os.chmod(provisioning, 0o755)
datasources = provisioning / "datasources"
datasources.mkdir(mode=0o755)
os.chmod(datasources, 0o755)
prom_config = config / "prometheus.yml"
prom_config.write_text("""global:
  scrape_interval: 15s
  scrape_timeout: 5s
  external_labels:
    monitor: staging-observability
scrape_configs:
  - job_name: prometheus
    static_configs:
      - targets: ['prometheus:9090']
""")
os.chmod(prom_config, 0o644)
source = datasources / "prometheus.yml"
source.write_text("""apiVersion: 1
datasources:
  - name: Prometheus
    uid: seonjalal-prometheus
    type: prometheus
    access: proxy
    url: http://prometheus:9090
    isDefault: true
    editable: false
""")
os.chmod(source, 0o644)
secret_dir = root / "secrets"
secret_dir.mkdir(mode=0o700)
password_file = secret_dir / "grafana-admin-password"
password_file.write_text(secrets.token_urlsafe(36))
os.chown(password_file, 472, 472)
os.chmod(password_file, 0o400)
(root / "image-lock.json").write_text(json.dumps(images, indent=2) + "\n")

# Prometheus 설정 구문 검사 후에만 서비스 기동. 원격 TSDB·관리 API 쓰기 기능 비활성.
docker("run", "--rm", "--network", "none", "--memory", "256m", "--memory-swap", "256m",
       "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
       "-v", str(prom_config) + ":/etc/prometheus/prometheus.yml:ro",
       "--entrypoint", "/bin/promtool", images["prometheus"]["digest"],
       "check", "config", "/etc/prometheus/prometheus.yml")
docker("network", "create", "seonjalal-monitor")
for volume in ("seonjalal-monitor-prometheus-data", "seonjalal-monitor-grafana-data"):
    docker("volume", "create", volume)

common = ["--detach", "--restart", "unless-stopped", "--network", "seonjalal-monitor",
          "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true", "--pids-limit", "256",
          "--log-driver", "json-file", "--log-opt", "max-size=10m", "--log-opt", "max-file=3"]
docker("run", *common, "--name", names[0], "--network-alias", "prometheus",
       "--memory", "640m", "--memory-swap", "640m", "--cpus", "0.75",
       "--publish", "127.0.0.1:9090:9090",
       "-v", str(prom_config) + ":/etc/prometheus/prometheus.yml:ro",
       "-v", "seonjalal-monitor-prometheus-data:/prometheus",
       images["prometheus"]["digest"], "--config.file=/etc/prometheus/prometheus.yml",
       "--storage.tsdb.path=/prometheus", "--storage.tsdb.retention.time=3d",
       "--storage.tsdb.retention.size=5GB", "--query.max-concurrency=4")
docker("run", *common, "--name", names[1], "--user", "472:472",
       "--memory", "384m", "--memory-swap", "384m", "--cpus", "0.5",
       "--publish", "127.0.0.1:3000:3000",
       "-e", "GF_SECURITY_ADMIN_USER=admin",
       "-e", "GF_SECURITY_ADMIN_PASSWORD__FILE=/run/secrets/grafana-admin-password",
       "-e", "GF_USERS_ALLOW_SIGN_UP=false", "-e", "GF_AUTH_ANONYMOUS_ENABLED=false",
       "-e", "GF_ANALYTICS_REPORTING_ENABLED=false", "-e", "GF_ANALYTICS_CHECK_FOR_UPDATES=false",
       "-e", "GF_ANALYTICS_CHECK_FOR_PLUGIN_UPDATES=false",
       "-v", str(password_file) + ":/run/secrets/grafana-admin-password:ro",
       "-v", str(provisioning) + ":/etc/grafana/provisioning:ro",
       "-v", "seonjalal-monitor-grafana-data:/var/lib/grafana",
       images["grafana"]["digest"])

def get_json(url):
    with opener.open(url, timeout=5) as response:
        return json.load(response)

ready = False
for attempt in range(30):
    try:
        with opener.open("http://127.0.0.1:9090/-/ready", timeout=3) as response:
            require(response.status == 200, "Prometheus readiness 필요")
        health = get_json("http://127.0.0.1:3000/api/health")
        targets = get_json("http://127.0.0.1:9090/api/v1/targets")
        ready = health.get("database") == "ok" and any(
            item.get("labels", {}).get("job") == "prometheus" and item.get("health") == "up"
            for item in targets.get("data", {}).get("activeTargets", []))
        if ready:
            break
    except (OSError, ValueError):
        pass
    time.sleep(2)
require(ready, "Health/자체 수집 미확인; 컨테이너/볼륨 보존 후 진단 필요")
auth = base64.b64encode(("admin:" + password_file.read_text()).encode()).decode()
request = urllib.request.Request("http://127.0.0.1:3000/api/datasources/uid/seonjalal-prometheus/health",
    headers={"Authorization": "Basic " + auth})
with opener.open(request, timeout=10) as response:
    datasource_health = json.load(response)
require(datasource_health.get("status") == "OK", "Grafana Prometheus 데이터 소스 확인 필요")
for name in names:
    detail = json.loads(docker("inspect", name))[0]
    require(detail["State"]["Running"] and not detail["State"]["OOMKilled"], "컨테이너 상태 확인 필요")
    ports = detail["NetworkSettings"]["Ports"]
    require(all(binding["HostIp"] == "127.0.0.1" for bindings in ports.values()
                for binding in (bindings or [])), "localhost 포트 바인딩 필요")
print("PROMETHEUS_READY_SELF_SCRAPE_UP")
print("GRAFANA_HEALTH_DATABASE_OK")
print("GRAFANA_PROMETHEUS_DATASOURCE_OK")
print("LOCALHOST_PORTS_ONLY=3000,9090")
print("GRAFANA_ADMIN_PASSWORD_FILE=" + str(password_file))
print("NO_PASSWORD_OUTPUT_NO_APP_DB_OR_SECURITY_GROUP_CHANGE")
print("LIMITATION=App,host,container,RDS,ALB metrics and dashboard verification pending")
'''


def build_request():
    remote = REMOTE.replace("__PROMETHEUS__", PROMETHEUS).replace("__GRAFANA__", GRAFANA)
    encoded = base64.b64encode(remote.encode()).decode()
    command = "python3 - <<'PY'\nimport base64\nexec(compile(base64.b64decode(" + repr(encoded) + "), '<observability-install>', 'exec'))\nPY"
    return {
        "DocumentName": "AWS-RunShellScript", "InstanceIds": [INSTANCE],
        "Comment": "Install private Prometheus/Grafana on staging monitoring host only",
        "TimeoutSeconds": 600,
        "Parameters": {"commands": [command], "executionTimeout": ["600"]},
    }


if __name__ == "__main__":
    request = build_request()
    compile(REMOTE, '<remote-static-validation>', 'exec')
    directory = Path(tempfile.mkdtemp(prefix="seonjalal-observability-install-", dir="/private/tmp"))
    os.chmod(directory, 0o700)
    path = directory / "request.json"
    path.write_text(json.dumps(request))
    os.chmod(path, 0o600)
    print("REQUEST_FILE=" + str(path))
    print("TARGET_INSTANCE=" + INSTANCE)
    print("PREPARED_ONLY_NO_AWS_CALL_OR_SERVER_CHANGE")
