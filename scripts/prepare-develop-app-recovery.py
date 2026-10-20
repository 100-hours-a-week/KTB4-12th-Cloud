#!/usr/bin/env python3
"""승인된 Develop 앱3개 복구 요청 준비. MySQL pull/재생성 제외."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("runtime", Path(__file__).with_name("prepare-develop-runtime-update.py"))
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)
BACKUP = "/opt/seonjalal-develop/db-before-recovery-x6q_we90"
DUMP_SHA = "e6f7da5cf97426ce28edb1274774e66f18f536db5197543597617cf32b3792e6"


def app_only_script(original):
    start = "# 정상 DB가 있으면 이미지 pull·컨테이너 재생성 없이 앱만 갱신한다.\n"
    end = 'SECRET_ENV_FILE="${secret_env_file}" "${deploy_path}/scripts/verify.sh" "${deploy_path}"'
    app_pull = 'sudo docker compose "${compose_args[@]}" pull main-backend payment-server frontend'
    loop_up = '  sudo docker compose "${compose_args[@]}" up --detach --no-deps --wait --wait-timeout 240 "${application_service}"'
    if original.count(start) != 1 or original.count(end) != 1:
        raise ValueError("원본 배포 스크립트 변경 감지. 복구 요청 준비 중단")
    deploy_block = original.split(start, 1)[1].split(end, 1)[0]
    if deploy_block.count(app_pull) != 1 or deploy_block.count(loop_up) != 1:
        raise ValueError("원본 배포 스크립트 변경 감지. 복구 요청 준비 중단")
    # 의존 서비스 자동 재생성을 차단하고 각 단계 healthy 이후 다음 앱으로 진행.
    replacement = app_pull + "\n" + "\n".join(
        'sudo docker compose "${compose_args[@]}" up --detach --no-deps --wait --wait-timeout 240 ' + service
        for service in ("main-backend", "payment-server", "frontend")
    ) + "\n\n"
    return original.split(start, 1)[0] + replacement + end + original.split(end, 1)[1]


REMOTE = r'''
import signal
os.umask(0o077)
backup = pathlib.Path(__BACKUP__)
if backup.is_symlink() or backup.resolve() != backup or not backup.is_dir():
    raise SystemExit("STOP: 승인된 DB 백업 경로 확인 필요")
record = json.loads((backup / "record.json").read_text())
digest = hashlib.sha256()
with open(backup / "develop.sql", "rb") as stream:
    for block in iter(lambda: stream.read(1048576), b""):
        digest.update(block)
if digest.hexdigest() != __DUMP_SHA__ or record.get("dump_sha256") != __DUMP_SHA__:
    raise SystemExit("STOP: 승인된 DB 백업 해시 불일치")
def db_state():
    result = subprocess.run(["docker", "inspect", "seonjalal-v2-develop-database-1"], capture_output=True, text=True, timeout=20)
    if result.returncode:
        raise SystemExit("STOP: DB 상태 확인 실패. 원문 비공개")
    value = json.loads(result.stdout)[0]
    mounts = [{"type": item.get("Type"), "name": item.get("Name"), "source": item.get("Source"),
               "destination": item.get("Destination"), "rw": item.get("RW")} for item in value.get("Mounts", [])]
    if (value["Id"], value["Image"], value["State"].get("StartedAt"), mounts) != (
        record["container_id"], record["image_id"], record["started_at"], record["mounts"]):
        raise SystemExit("STOP: 백업 이후 DB 컨테이너/이미지/저장소 변경 감지")
    if value["State"].get("Health", {}).get("Status") != "healthy":
        raise SystemExit("STOP: DB healthy 아님")

db_state()
# 서버 배포파일이 준비 이후 달라졌으면 임의 실행하지 않음.
expected = __EXPECTED_FILES__
for relative, sha in expected.items():
    path = root / relative
    if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != sha:
        raise SystemExit("STOP: 배포파일 변경 감지")
env_before = (root / ".env").read_bytes()
settings = dict(line.split("=", 1) for line in env_before.decode().splitlines() if "=" in line and not line.startswith("#"))
for key, value in __EXPECTED_ENV__.items():
    if settings.get(key) != value:
        raise SystemExit("STOP: 승인된 이미지/DB 설정과 불일치")
directory = pathlib.Path(tempfile.mkdtemp(prefix="app-recovery-", dir=root))
os.chmod(directory, 0o700)
script = directory / "deploy-apps-only.sh"
script.write_text(__APP_SCRIPT__)
os.chmod(script, 0o700)
subprocess.run(["bash", "-n", str(script)], check=True, capture_output=True)
print("RECOVERY_LOG=" + str(directory / "deploy.log"), flush=True)
with open(directory / "deploy.log", "xb") as log:
    child = subprocess.Popen(["bash", str(script), str(root)], stdout=log, stderr=subprocess.STDOUT,
                             start_new_session=True)
    try:
        code = child.wait(timeout=900)
    except subprocess.TimeoutExpired:
        os.killpg(child.pid, signal.SIGTERM)
        try:
            child.wait(timeout=15)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait()
        code = 124
db_state()
if (root / ".env").read_bytes() != env_before:
    raise SystemExit("STOP: 복구 중 .env 변경 감지. 추가 재배포 금지")
print("MYSQL_CONTAINER_IMAGE_AND_STORAGE_UNCHANGED")
print("RECOVERY_EXIT_CODE=" + str(code))
if code:
    raise SystemExit("STOP: 앱 복구 실패. 원문 로그 비공개, 자동 DB복원/과거이미지전환 없음")
print("DEVELOP_APPS_RECOVERED_AND_LOCAL_HEALTH_VERIFIED")
print("PRODUCTION_AND_STAGING_UNCHANGED_AI_FUNCTIONAL_QA_PENDING")
'''


def build_request():
    import hashlib
    spec = importlib.util.spec_from_file_location("env_update", Path(__file__).with_name("prepare-develop-env-update.py"))
    env_update = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(env_update)
    source = ROOT / "operations/develop/runtime"
    script = app_only_script((source / "scripts/deploy.sh").read_text())
    expected = {relative: hashlib.sha256((source / relative).read_bytes()).hexdigest() for relative in runtime.FILES}
    env = {**env_update.IMAGES, "DB_NAME": "seonjalal_develop", "DB_USER": "seonjalal_develop",
           "RUNTIME_SECRET_ID": "/seonjalal/develop/runtime", "PRODUCT_SEARCH_ENABLED": "false"}
    remote = runtime.REMOTE.split("files = __PUBLIC_FILES__", 1)[0] + REMOTE.replace(
        "__BACKUP__", repr(BACKUP)).replace("__DUMP_SHA__", repr(DUMP_SHA)).replace(
        "__EXPECTED_FILES__", repr(expected)).replace("__EXPECTED_ENV__", repr(env)).replace("__APP_SCRIPT__", repr(script))
    compile(remote, "<develop-app-recovery>", "exec")
    return {"DocumentName": "AWS-RunShellScript", "InstanceIds": [runtime.INSTANCE],
            "Comment": "Approved Develop app recovery; preserve MySQL and other environments",
            "TimeoutSeconds": 1020, "Parameters": {"commands": ["python3 - <<'PY'\n" + remote + "\nPY"],
                                                    "executionTimeout": ["1020"]}}


def main():
    os.umask(0o077)
    request = build_request()
    target = Path(tempfile.mkdtemp(prefix="seonjalal-develop-app-recovery.", dir="/private/tmp")) / "request.json"
    target.write_text(json.dumps(request))
    print("SSM_REQUEST_FILE=" + str(target))
    print("LOCAL_PREPARE_ONLY_NO_AWS_OR_SERVER_CHANGE")


if __name__ == "__main__":
    main()
