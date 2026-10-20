#!/usr/bin/env python3
"""Mac의 로컬 SSM 요청 파일만 준비. 이 도구 자체는 AWS·Docker를 호출하지 않는다."""
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shlex
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "operations/staging/frontend-gateway"
INSTANCE = "i-06f8ccdce3a1e9ff4"
TARGET = "/opt/seonjalal-staging-gateway"
IMAGE = "peterj0117/seonjalal-fe@sha256:274aee76a923e1eed970bf6bdb65538754b35608110624ba0cc441b330276e61"


def build_script(files):
    payload = json.dumps({key: base64.b64encode(value).decode() for key, value in files.items()})
    writer = (
        "import base64,json,os; from pathlib import Path; "
        "p=Path(" + repr(TARGET) + "); "
        "data=json.loads(" + repr(payload) + "); "
        "[(p.joinpath(k).write_bytes(base64.b64decode(v)),os.chmod(p/k,0o644)) for k,v in data.items()]"
    )
    script = r'''set -Eeuo pipefail
target=/opt/seonjalal-staging-gateway
project=seonjalal-staging-gateway
started=0
on_error() {
  code=$?
  trap - ERR
  printf 'STOP_GATEWAY_STAGE_EXIT=%s\n' "$code"
  if [ "$started" = 1 ]; then
    # 이 실행이 새로 만든 독립 서비스만 중지. 파일·로그·기존 Develop 서비스는 보존.
    docker compose --project-name "$project" --file "$target/compose.yaml" stop gateway || true
    printf 'NEW_GATEWAY_STOP_REQUESTED_EXISTING_SERVICES_UNCHANGED\n'
  fi
  exit "$code"
}
trap on_error ERR
test "$(id -u)" = 0
date -u '+GATEWAY_PREFLIGHT_UTC=%Y-%m-%dT%H:%M:%SZ'

# IMDSv2로 실제 대상 검증. 관리 토큰은 출력·저장하지 않는다.
metadata_token=$(curl --noproxy '*' -fsS --connect-timeout 2 --max-time 5 \
  -X PUT -H 'X-aws-ec2-metadata-token-ttl-seconds: 60' http://169.254.169.254/latest/api/token)
actual_instance=$(curl --noproxy '*' -fsS --connect-timeout 2 --max-time 5 \
  -H "X-aws-ec2-metadata-token: $metadata_token" http://169.254.169.254/latest/meta-data/instance-id)
test "$actual_instance" = i-06f8ccdce3a1e9ff4
unset metadata_token
test "$(curl -4 -fsS --connect-timeout 5 --max-time 15 https://checkip.amazonaws.com)" = 3.34.200.35
test ! -e "$target"
test -z "$(ss -H -lnt 'sport = :18082')"
test -z "$(docker ps -aq --filter label=com.docker.compose.project=seonjalal-staging-gateway)"
available_kib=$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)
printf 'HOST_MEM_AVAILABLE_KIB=%s\n' "$available_kib"
test "$available_kib" -ge 262144
docker compose version --short
command -v python3 >/dev/null
serve_before=$(tailscale serve status --json)
printf 'GATEWAY_PREFLIGHT_OK\n'

# 신규 폴더를 원자적으로 생성하므로 기존 설정 덮어쓰기·동일 작업 중복 실행 금지.
umask 022
mkdir "$target"
__WRITE_FILES__
install -d -m 0700 "$target/verification"
printf '%s\n' "$serve_before" > "$target/verification/tailscale-before.json"
chmod 0600 "$target/verification/tailscale-before.json"
sha256sum "$target/compose.yaml" "$target/nginx.conf"
docker pull --platform linux/amd64 __IMAGE__
test "$(docker image inspect __IMAGE__ --format '{{.Os}}/{{.Architecture}}')" = linux/amd64
docker compose --project-name "$project" --file "$target/compose.yaml" config --quiet

# 허용된 고정 이미지에서 네트워크 없는 임시 Nginx 구문 검사 후 자체 컨테이너만 정리.
docker run --rm --pull=never --platform linux/amd64 --network none \
  --read-only --user 101:101 --cap-drop ALL --security-opt no-new-privileges \
  --tmpfs /tmp:rw,nosuid,nodev,noexec,size=32m,mode=1777 \
  --mount "type=bind,source=$target/nginx.conf,target=/etc/nginx/nginx.conf,readonly" \
  --entrypoint nginx __IMAGE__ -t
started=1
docker compose --project-name "$project" --file "$target/compose.yaml" up \
  --detach --no-build --pull never --wait --wait-timeout 60 gateway

# 읽기 전용 요청만 사용. Origin 검사·브라우저 로그인·Tailnet 포트는 아직 별도 단계.
umask 077
curl -fsS --connect-timeout 5 --max-time 20 http://127.0.0.1:18082/gateway-health
curl -fsS --connect-timeout 5 --max-time 20 \
  -o "$target/verification/index.html" \
  -w 'GATEWAY_INDEX_HTTP=%{http_code}\nGATEWAY_INDEX_SECONDS=%{time_total}\n' \
  http://127.0.0.1:18082/index.html
test "$(sha256sum "$target/verification/index.html" | awk '{print $1}')" = 370833ee43e36d3bdd257f18e301f1811f84333edc424c19ca5f6048c5e0e681
sha256sum "$target/verification/index.html"
test "$(curl -sS --connect-timeout 5 --max-time 20 -o /dev/null -w '%{http_code}' \
  http://127.0.0.1:18082/api/actuator/health)" = 200
printf 'GATEWAY_API_HEALTH_HTTP=200\n'
test "$(curl -sS --connect-timeout 5 --max-time 20 -o /dev/null -w '%{http_code}' \
  'http://127.0.0.1:18082/api/products?sort=POPULAR')" = 200
printf 'GATEWAY_PRODUCTS_HTTP=200\n'
test "$(tailscale serve status --json)" = "$serve_before"
docker compose --project-name "$project" --file "$target/compose.yaml" ps
date -u '+GATEWAY_COMPLETED_UTC=%Y-%m-%dT%H:%M:%SZ'
printf 'GATEWAY_LOOPBACK_READY_TAILSCALE_AND_BROWSER_AUTH_PENDING\n'
'''
    return script.replace("__WRITE_FILES__", "python3 -c " + shlex.quote(writer)).replace("__IMAGE__", shlex.quote(IMAGE))


def main():
    os.umask(0o077)
    files = {name: (SOURCE / name).read_bytes() for name in ("compose.yaml", "nginx.conf")}
    script = build_script(files)
    request = {
        "DocumentName": "AWS-RunShellScript",
        "InstanceIds": [INSTANCE],
        "Comment": "Approved staging-only loopback gateway; preserve Develop and Tailscale",
        "TimeoutSeconds": 600,
        "Parameters": {"commands": ["bash -c " + shlex.quote(script)], "executionTimeout": ["600"]},
    }
    directory = Path(tempfile.mkdtemp(prefix="seonjalal-staging-gateway.", dir="/private/tmp"))
    (directory / "request.json").write_text(json.dumps(request, indent=2) + "\n")
    (directory / "remote-script.sh").write_text(script)
    manifest = {
        "prepared_utc": datetime.now(timezone.utc).isoformat(),
        "target_instance": INSTANCE, "target_path": TARGET,
        "script_sha256": hashlib.sha256(script.encode()).hexdigest(),
        "files": {key: hashlib.sha256(value).hexdigest() for key, value in files.items()},
        "aws_executed": False,
    }
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print("GATEWAY_PREPARE_DIR=" + str(directory))
    print("SSM_REQUEST_FILE=" + str(directory / "request.json"))
    print("REMOTE_SCRIPT_SHA256=" + manifest["script_sha256"])
    print("LOCAL_PREPARE_ONLY_NO_AWS_OR_SERVER_CHANGE")


if __name__ == "__main__":
    main()
