#!/usr/bin/env python3
"""Mac: 기존 Prometheus 보존형 변경안·검사 SSM 요청 생성. AWS 호출·실제 전환 없음."""
import argparse
import base64
import json
import os
from pathlib import Path
import re
import tempfile
import textwrap

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "prom/prometheus@sha256:efd719c99d83b060d9daefdcf00360461adf279f45ef5391f8d111892118753e"
REMOTE = r'''
import base64, copy, datetime, hashlib, json, os, pathlib, stat, subprocess, urllib.request, uuid
import yaml

def require(ok, reason):
    if not ok:
        raise SystemExit("STOP: " + reason)

def command(args, timeout=30):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    require(result.returncode == 0, "검사 실패 exit=" + str(result.returncode) +
            "; 비밀값 보호를 위해 원문 미출력")
    return result.stdout

def write(path, content, mode=0o600):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, "wb") as output:
        output.write(content)
        output.flush()
        os.fchmod(output.fileno(), mode)
        os.fsync(output.fileno())

def dump(path, value):
    write(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode())

require(os.geteuid() == 0, "root 실행 필요")
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
base = "http://169.254.169.254/latest/"
request = urllib.request.Request(base + "api/token", method="PUT",
    headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"})
with opener.open(request, timeout=3) as response:
    token = response.read().decode()
request = urllib.request.Request(base + "dynamic/instance-identity/document",
    headers={"X-aws-ec2-metadata-token": token})
with opener.open(request, timeout=3) as response:
    identity = json.load(response)
require(identity.get("instanceId") == __INSTANCE__ and
        identity.get("region") == "ap-northeast-2", "관측 서버 대상 불일치")

root = pathlib.Path("/opt/seonjalal-observability")
source = root / "config/prometheus.yml"
for path in (root, root / "config", source, root / "changes"):
    require(not path.is_symlink(), "심볼릭 링크 경로 중단")
require(source.is_file(), "기존 Prometheus 설정 필요")
original = source.read_bytes()
current = yaml.safe_load(original)
require(isinstance(current, dict) and isinstance(current.get("scrape_configs"), list),
        "기존 설정 구조 확인 필요")
require(not current.get("rule_files"), "기존 rule_files 존재 — 보존·검증 경로 추가 검토 필요")
candidate = copy.deepcopy(current)

detail = json.loads(command(["docker", "inspect", "seonjalal-monitor-prometheus"]))[0]
require(detail["State"]["Running"], "기존 Prometheus 실행 필요")
require(detail["Config"]["Image"] == __IMAGE__, "기존 이미지 변경 — 재검토 필요")
host = detail["HostConfig"]
require(host["NetworkMode"] == "seonjalal-monitor", "기존 network 변경")
require(host["Memory"] == 671088640 and host["NanoCpus"] == 750000000,
        "기존 자원 상한 변경")
require(host["PortBindings"] == {"9090/tcp": [{"HostIp": "127.0.0.1", "HostPort": "9090"}]},
        "기존 포트 게시 변경")
require(detail["Config"]["User"] == "nobody" and not host["ReadonlyRootfs"] and
        host["RestartPolicy"]["Name"] == "unless-stopped", "기존 실행 조건 변경")
mounts = detail["Mounts"]
require(len(mounts) == 2, "기존 마운트 추가/변경 — 재검토 필요")
require(any(m["Type"] == "volume" and m.get("Name") == "seonjalal-monitor-prometheus-data"
            and m["Destination"] == "/prometheus" and m["RW"] for m in mounts),
        "기존 TSDB 볼륨 불일치")
require(any(m["Type"] == "bind" and m["Source"] == str(source) and
            m["Destination"] == "/etc/prometheus/prometheus.yml" and not m["RW"] for m in mounts),
        "기존 config 마운트 불일치")
args = detail["Config"]["Cmd"]
for expected in ("--storage.tsdb.retention.time=3d", "--storage.tsdb.retention.size=5GB",
                 "--query.max-concurrency=4", "--config.file=/etc/prometheus/prometheus.yml",
                 "--storage.tsdb.path=/prometheus"):
    require(expected in args, "기존 실행 옵션 변경")
require("--web.enable-remote-write-receiver" not in args, "이미 수신 활성화 상태 — 재검토 필요")

alerting = candidate.setdefault("alerting", {})
require(isinstance(alerting, dict), "기존 alerting 구조 확인 필요")
managers = alerting.setdefault("alertmanagers", [])
require(isinstance(managers, list), "기존 Alertmanager 구조 확인 필요")
new_manager = {"static_configs": [{"targets": ["alertmanager:9093"]}]}
require(new_manager not in managers, "이미 Alertmanager 연결 존재")
managers.append(new_manager)
rule_target = "/etc/prometheus/rules/payment-rabbitmq-alerts.yml"
candidate["rule_files"] = [rule_target]
require(candidate["scrape_configs"] == current["scrape_configs"], "기존 job 변경 차단")
require(candidate.get("global") == current.get("global"), "기존 global 변경 차단")
other = copy.deepcopy(candidate)
if "alerting" in current:
    other["alerting"] = current["alerting"]
else:
    other.pop("alerting", None)
other.pop("rule_files", None)
require(other == {k: v for k, v in current.items() if k != "rule_files"},
        "허용 범위 밖 설정 변경 차단")

changes = root / "changes"
changes.mkdir(mode=0o700, exist_ok=True)
require(changes.stat().st_uid == 0 and stat.S_IMODE(changes.stat().st_mode) == 0o700,
        "변경안 디렉터리 root/0700 필요")
stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
directory = changes / ("prometheus-v3-" + stamp + "-" + uuid.uuid4().hex[:6])
directory.mkdir(mode=0o700)
write(directory / "prometheus-before.yml", original)
# Docker inspect에는 환경변수가 포함될 수 있어 서버 root 전용 파일로만 보존.
dump(directory / "container-before.json", detail)
encoded = yaml.safe_dump(candidate, allow_unicode=True, sort_keys=False).encode()
write(directory / "prometheus-candidate.yml", encoded, 0o644)
for filename, payload in __FILES__.items():
    write(directory / filename, base64.b64decode(payload), 0o644)

common = ["docker", "run", "--rm", "--pull=never", "--read-only", "--network", "none",
          "--user", "65534:65534", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
          "--memory", "256m", "--cpus", "0.5", "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m,mode=1777",
          "--mount", "type=bind,src=" + str(directory) + ",dst=/check,readonly",
          "--mount", "type=bind,src=" + str(directory / "payment-rabbitmq-alerts.yml") +
          ",dst=" + rule_target + ",readonly", "--workdir", "/check", "--entrypoint", "/bin/promtool", __IMAGE__]
# 상위 changes 디렉터리는 root 전용 유지. 컨테이너에 bind한 검사 디렉터리만 읽기 허용.
os.chmod(directory, 0o755)
try:
    command(common + ["check", "config", "--syntax-only", "/check/prometheus-candidate.yml"], 60)
    command(common + ["check", "rules", "/check/payment-rabbitmq-alerts.yml"], 60)
    command(common + ["test", "rules", "/check/payment-rabbitmq-alerts.test.yml"], 90)
finally:
    os.chmod(directory, 0o700)

# 실설정·기존 컨테이너는 유지. 준비 중 기준이 바뀌었으면 다음 적용 전 중단.
require(source.read_bytes() == original, "준비 중 설정 변경 감지")
latest = json.loads(command(["docker", "inspect", "seonjalal-monitor-prometheus"]))[0]
require(latest["Id"] == detail["Id"] and latest["State"]["Running"], "기존 컨테이너 변경 감지")
plan = {
    "preparedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "directory": str(directory),
    "beforeConfigSha256": hashlib.sha256(original).hexdigest(),
    "candidateConfigSha256": hashlib.sha256(encoded).hexdigest(),
    "beforeContainerId": detail["Id"],
    "existingJobs": [job["job_name"] for job in current["scrape_configs"]],
    "existingJobsPreserved": True,
    "existingGlobalPreserved": True,
    "existingCommandPreserved": True,
    "existingTsdbVolumePreserved": True,
    "memoryMiB": host["Memory"] // 1024**2,
    "cpuLimit": host["NanoCpus"] / 10**9,
    "newFlag": "--web.enable-remote-write-receiver",
    "proposedPrivateBinding": identity["privateIp"] + ":9090",
    "requiredIngress": "EKS node SG only; TCP9090; approval and actual connectivity pending",
    "alertmanagerTarget": "alertmanager:9093",
    "paymentAlertRules": 5,
    "checks": ["promtool config syntax", "promtool rules", "promtool empty/old DLQ tests"],
    "liveConfigChanged": False,
    "containerRestarted": False,
    "securityGroupChanged": False,
}
dump(directory / "plan.json", plan)
print("PROMETHEUS_PREPARE_CHECK_FINISHED")
print(json.dumps(plan, ensure_ascii=False))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance-id", required=True)
    parser.add_argument("--output", type=Path,
                        default=Path("/private/tmp/seonjalal-v3-monitoring/prepare-prometheus.json"))
    args = parser.parse_args()
    if not re.fullmatch(r"i-(?:[0-9a-f]{8}|[0-9a-f]{17})", args.instance_id):
        parser.error("EC2 instance-id 형식 확인 필요")
    files = {name: base64.b64encode((ROOT / "operations/monitoring/prometheus" / name).read_bytes()).decode()
             for name in ("payment-rabbitmq-alerts.yml", "payment-rabbitmq-alerts.test.yml")}
    remote = REMOTE.replace("__INSTANCE__", repr(args.instance_id)).replace("__IMAGE__", repr(IMAGE))
    remote = remote.replace("__FILES__", repr(files))
    # YAML/호스트 오류가 기존 설정의 Secret 원문을 포함해도 SSM 출력으로 노출하지 않음.
    remote = ("try:\n" + textwrap.indent(remote, "    ") +
              "\nexcept SystemExit:\n    raise\nexcept Exception as error:\n" +
              "    print('STOP: ' + type(error).__name__ + '; 오류 원문 미출력')\n" +
              "    raise SystemExit(1)\n")
    compile(remote, "<remote-prometheus-prepare>", "exec")
    request = {
        "DocumentName": "AWS-RunShellScript", "InstanceIds": [args.instance_id],
        "Comment": "Prepare preserved Prometheus config and validate rules; no live restart",
        "TimeoutSeconds": 300,
        "Parameters": {"executionTimeout": ["300"], "commands": ["python3 - <<'PY'\n" + remote + "\nPY"]},
    }
    args.output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=args.output.parent, prefix=".prometheus-prepare-")
    try:
        with os.fdopen(fd, "w") as output:
            json.dump(request, output, ensure_ascii=False, indent=2)
            output.write("\n")
        os.replace(temporary, args.output)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print("SSM 요청 파일:", args.output)
    print("AWS 호출 없음 / 기존 job·global 보존형 후보와 경고5개 검사만 준비 / 실제 재기동 없음")


if __name__ == "__main__":
    main()
