#!/usr/bin/env python3
"""Mac: 관측 서버의 Alertmanager 파일 준비·검사 SSM 요청 생성. AWS 호출 없음."""
import argparse
import base64
import json
import os
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "prom/alertmanager@sha256:58e117eabccebbff04e6643a3432d6315a2cc3a8c24ab5849bc628886bf08857"
REMOTE = r'''
import base64, hashlib, json, os, pathlib, stat, subprocess, tempfile, urllib.request

def require(condition, reason):
    if not condition:
        raise SystemExit("STOP: " + reason)

require(os.geteuid() == 0, "root 실행 필요")
# SSM 대상을 잘못 지정해도 다른 EC2의 파일을 만들기 전에 중단.
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
secret = root / "secrets/alertmanager/discord-webhook-url"
directory = root / "config/alertmanager"
target = directory / "alertmanager.yml"
# 값은 읽지 않고 소유·읽기 권한만 검사. 기존 Secret 재발급·덮어쓰기 없음.
for path in (root, root / "secrets", secret.parent, secret, root / "config", directory, target):
    require(not path.is_symlink(), "심볼릭 링크 경로 중단")
require(root.is_dir() and (root / "config").is_dir(), "기존 관측 설치 경로 필요")
info = secret.stat()
require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_gid == 65534
        and stat.S_IMODE(info.st_mode) == 0o440, "Secret root:65534/0440 필요")
content = base64.b64decode(__CONFIG__)
directory.mkdir(mode=0o755, exist_ok=True)
require(stat.S_IMODE(directory.stat().st_mode) == 0o755 and
        directory.stat().st_uid == 0, "설정 디렉터리 root/0755 필요")
if target.exists():
    require(target.is_file() and target.read_bytes() == content,
            "기존 설정과 다름 — 덮어쓰기 없이 중단")
else:
    fd, temporary = tempfile.mkstemp(dir=directory, prefix=".alertmanager-")
    try:
        with os.fdopen(fd, "wb") as output:
            output.write(content)
            output.flush()
            os.fchmod(output.fileno(), 0o644)
            os.fsync(output.fileno())
        os.link(temporary, target)
    finally:
        os.unlink(temporary)
require(target.stat().st_uid == 0 and stat.S_IMODE(target.stat().st_mode) == 0o644,
        "설정 파일 root/0644 필요")

# 검사 컨테이너는 통신 불가·읽기 전용. 서비스 기동 및 메시지 전송 없음.
command = ["docker", "run", "--rm", "--pull=never", "--read-only", "--network", "none",
    "--user", "65534:65534", "--cap-drop", "ALL",
    "--security-opt", "no-new-privileges:true",
    "--mount", "type=bind,src=" + str(target) + ",dst=/etc/alertmanager/alertmanager.yml,readonly",
    "--mount", "type=bind,src=" + str(secret) + ",dst=/run/secrets/discord-webhook-url,readonly",
    "--entrypoint", "/bin/amtool", __IMAGE__,
    "check-config", "/etc/alertmanager/alertmanager.yml"]
result = subprocess.run(command, capture_output=True, text=True, timeout=60)
# 검증 오류가 파일 내용이나 URL을 포함할 가능성에 대비하여 원문 미출력.
require(result.returncode == 0, "amtool 검사 실패 exit=" + str(result.returncode) +
        "; 원문 미출력, 파일 삭제·서비스 기동 금지")
print("ALERTMANAGER_CONFIG_CHECK_FINISHED")
print(json.dumps({"configSha256": hashlib.sha256(content).hexdigest(),
    "user": "65534:65534", "secretMode": "0440", "network": "none",
    "productionRepeat": "4h", "resolvedEnabled": True,
    "serviceStarted": False, "discordSent": False}))
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instance-id", required=True)
    parser.add_argument("--output", type=Path,
                        default=Path("/private/tmp/seonjalal-v3-monitoring/prepare-alertmanager.json"))
    args = parser.parse_args()
    if not (args.instance_id.startswith("i-") and
            len(args.instance_id) in (10, 19) and
            all(c in "0123456789abcdef" for c in args.instance_id[2:])):
        parser.error("EC2 instance-id 형식 확인 필요")
    config = (ROOT / "operations/monitoring/alertmanager/alertmanager.yml").read_bytes()
    remote = REMOTE.replace("__INSTANCE__", repr(args.instance_id))
    remote = remote.replace("__CONFIG__", repr(base64.b64encode(config).decode()))
    remote = remote.replace("__IMAGE__", repr(IMAGE))
    # 생성된 원격 Python 코드의 문법 오류를 AWS 요청 전 검출.
    compile(remote, "<remote-alertmanager-prepare>", "exec")
    request = {
        "DocumentName": "AWS-RunShellScript",
        "InstanceIds": [args.instance_id],
        "Comment": "V3 Alertmanager config preparation and offline validation only",
        "TimeoutSeconds": 120,
        "Parameters": {"executionTimeout": ["120"],
                       "commands": ["python3 - <<'PY'\n" + remote + "\nPY"]},
    }
    args.output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=args.output.parent, prefix=".request-")
    try:
        with os.fdopen(fd, "w") as output:
            json.dump(request, output, ensure_ascii=False, indent=2)
            output.write("\n")
        os.replace(temporary, args.output)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print("SSM 요청 파일:", args.output)
    print("범위: 설정 파일 준비·amtool 검사 / AWS 호출·서비스 기동·Discord 전송 없음")


if __name__ == "__main__":
    main()
