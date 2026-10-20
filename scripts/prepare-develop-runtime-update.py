#!/usr/bin/env python3
"""비밀 없는 Develop 배포파일 갱신 요청 준비. AWS 호출·서버 변경 없음."""
import base64
import hashlib
import json
import os
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parents[1]
FILES = ("compose.yaml", "scripts/deploy.sh", "scripts/verify.sh")
INSTANCE = "i-06f8ccdce3a1e9ff4"

REMOTE = r'''
import base64, fcntl, hashlib, json, os, pathlib, shutil, stat, subprocess, tempfile, urllib.request

# IMDSv2로 대상 계정·리전·인스턴스를 확인. 잘못된 서버에서는 파일 접근 전 중단.
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
request = urllib.request.Request("http://169.254.169.254/latest/api/token", method="PUT",
    headers={"X-aws-ec2-metadata-token-ttl-seconds": "60"})
with opener.open(request, timeout=3) as response:
    token = response.read().decode()
request = urllib.request.Request("http://169.254.169.254/latest/dynamic/instance-identity/document",
    headers={"X-aws-ec2-metadata-token": token})
with opener.open(request, timeout=3) as response:
    identity = json.load(response)
if (identity.get("accountId"), identity.get("region"), identity.get("instanceId")) != (
    "721744297924", "ap-northeast-2", "i-06f8ccdce3a1e9ff4"):
    raise SystemExit("STOP: Develop 대상 불일치")

root = pathlib.Path("/opt/seonjalal-develop")
if root.is_symlink() or root.resolve() != root or not root.is_dir():
    raise SystemExit("STOP: 배포 경로 확인 필요")
lock_path = root / ".deploy.lock"
if lock_path.is_symlink() or not lock_path.is_file():
    raise SystemExit("STOP: 기존 배포 잠금 파일 확인 필요")
lock = os.open(lock_path, os.O_RDWR | os.O_NOFOLLOW)
try:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    raise SystemExit("STOP: 다른 배포가 진행 중. 종료하지 말고 대기 필요")

files = __PUBLIC_FILES__
decoded = {}
for relative, entry in files.items():
    path = root / relative
    if path.is_symlink() or path.parent.resolve() != path.parent or not path.is_file():
        raise SystemExit("STOP: 기존 배포파일 경로 확인 필요")
    data = base64.b64decode(entry["base64"], validate=True)
    if hashlib.sha256(data).hexdigest() != entry["sha256"]:
        raise SystemExit("STOP: 전송 파일 해시 불일치")
    decoded[relative] = data

# 기존 파일을 보존하고 파일별 원자 교체. .env·Secret·컨테이너·DB는 수정하지 않음.
backup = pathlib.Path(tempfile.mkdtemp(prefix="runtime-files-before-", dir=root))
os.chmod(backup, 0o700)
for relative in files:
    target = backup / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(root / relative, target)

changed = []
def replace(relative, data):
    path = root / relative
    info = path.stat()
    descriptor, name = tempfile.mkstemp(prefix=".runtime-update-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(name, stat.S_IMODE(info.st_mode))
        os.chown(name, info.st_uid, info.st_gid)
        if relative.endswith(".sh"):
            subprocess.run(["bash", "-n", name], check=True, capture_output=True)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)

try:
    for relative, data in decoded.items():
        changed.append(relative)
        replace(relative, data)
except Exception:
    for relative in reversed(changed):
        replace(relative, (backup / relative).read_bytes())
    raise SystemExit("STOP: 파일 교체 실패. 원본 복원 수행, 백업 확인 필요") from None

print("DEVELOP_RUNTIME_FILES_UPDATED=" + str(len(files)))
for relative, data in decoded.items():
    print("FILE_SHA256=" + relative + ":" + hashlib.sha256(data).hexdigest())
print("BACKUP_DIR=" + str(backup))
print("NO_ENV_SECRET_CONTAINER_DB_TAILSCALE_OR_STAGING_CHANGE")
'''


def build_request():
    files = {}
    for relative in FILES:
        data = (ROOT / "operations/develop/runtime" / relative).read_bytes()
        files[relative] = {"base64": base64.b64encode(data).decode(),
                           "sha256": hashlib.sha256(data).hexdigest()}
    script = REMOTE.replace("__PUBLIC_FILES__", repr(files))
    compile(script, "<develop-runtime-update>", "exec")
    return {
        "DocumentName": "AWS-RunShellScript", "InstanceIds": [INSTANCE],
        "Comment": "Update Develop runtime files only; preserve env and containers",
        "TimeoutSeconds": 120,
        "Parameters": {"commands": ["python3 - <<'PY'\n" + script + "\nPY"],
                       "executionTimeout": ["120"]},
    }


def main():
    os.umask(0o077)
    request = build_request()
    directory = Path(tempfile.mkdtemp(prefix="seonjalal-develop-runtime-update.", dir="/private/tmp"))
    path = directory / "request.json"
    path.write_text(json.dumps(request))
    os.chmod(path, 0o600)
    print("SSM_REQUEST_FILE=" + str(path))
    print("PUBLIC_FILES=3")
    print("LOCAL_PREPARE_ONLY_NO_AWS_OR_SERVER_CHANGE")


if __name__ == "__main__":
    main()
