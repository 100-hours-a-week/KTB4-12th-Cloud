#!/usr/bin/env python3
"""승인된 Develop 공개 설정·이미지 참조 갱신 요청 준비. 실제 AWS 호출 없음."""
import argparse
import importlib.util
import inspect
import json
import os
from pathlib import Path
import re
import tempfile


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runtime = load("runtime_update", "prepare-develop-runtime-update.py")
merge = load("secret_merge", "merge-develop-runtime-secret.py")
PREFIX = "721744297924.dkr.ecr.ap-northeast-2.amazonaws.com/seonjalal/develop/"
NEW_SHA = "f8fc155dc44c2b48244880c13e297f8c49587580"
OLD_SHA = "44741fc5336329712a2c1b3641e4dd13269d2855"
IMAGES = {
    "FRONTEND_IMAGE": PREFIX + "frontend:develop-63013155fbc07b78486e3fab87b8aa8913e06da6",
    "MAIN_BACKEND_IMAGE": PREFIX + "backend:develop-main-backend-" + NEW_SHA,
    "PAYMENT_SERVER_IMAGE": PREFIX + "backend:develop-payment-server-" + NEW_SHA,
}
OLD_IMAGES = {
    "FRONTEND_IMAGE": PREFIX + "frontend:develop-efaa3c25e7e6436ec769ac6550ba6bd41c938bb9",
    "MAIN_BACKEND_IMAGE": PREFIX + "backend:develop-main-backend-" + OLD_SHA,
    "PAYMENT_SERVER_IMAGE": PREFIX + "backend:develop-payment-server-" + OLD_SHA,
}


def patch_env(text, updates, old_images):
    # 승인 키만 교체하고 기존 DB/JWT 등 나머지 줄은 그대로 보존.
    lines = text.splitlines(keepends=True)
    found = {}
    for index, line in enumerate(lines):
        match = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line.rstrip("\r\n"))
        if match and match[1] in updates:
            key = match[1]
            if key in found:
                raise ValueError("갱신 대상 공개 설정 중복. 변경 중단")
            found[key] = (index, match[2])
    for key, old in old_images.items():
        if key not in found or found[key][1] not in (old, updates[key]):
            raise ValueError("이미지 설정 변경 감지. 변경 중단")
    for key, value in updates.items():
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or not re.fullmatch(r"[A-Za-z0-9_./:@-]+", value):
            raise ValueError("공개 설정 형식 확인 필요")
        if key in found:
            lines[found[key][0]] = key + "=" + value + "\n"
        else:
            if lines and not lines[-1].endswith("\n"):
                lines[-1] += "\n"
            lines.append(key + "=" + value + "\n")
    return "".join(lines)


REMOTE = r'''
import re
__PATCH_FUNCTION__
updates = __UPDATES__
old_images = __OLD_IMAGES__
expected_images = __IMAGES__
services = {"FRONTEND_IMAGE": "frontend", "MAIN_BACKEND_IMAGE": "main-backend", "PAYMENT_SERVER_IMAGE": "payment-server"}
# 컨테이너는 검사만 수행. 다른 배포로 대상이 달라지면 쓰기 전 중단.
for key, service in services.items():
    value = subprocess.check_output(["docker", "inspect", "--format", "{{.Config.Image}}",
        "seonjalal-v2-develop-" + service + "-1"], text=True).strip()
    if value != expected_images[key]:
        raise SystemExit("STOP: 실행 이미지 변경 감지")
path = root / ".env"
if path.is_symlink() or not path.is_file():
    raise SystemExit("STOP: .env 경로 확인 필요")
before = path.read_text()
after = patch_env(before, updates, old_images)
backup = pathlib.Path(tempfile.mkdtemp(prefix="env-before-recovery-", dir=root))
os.chmod(backup, 0o700)
shutil.copy2(path, backup / ".env")
os.chmod(backup / ".env", 0o600)
info = path.stat()
descriptor, name = tempfile.mkstemp(prefix=".env-recovery-", dir=root)
try:
    with os.fdopen(descriptor, "w") as stream:
        stream.write(after)
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(name, 0o600)
    os.chown(name, info.st_uid, info.st_gid)
    if path.read_text() != before:
        raise SystemExit("STOP: .env 동시 변경 감지. 백업 보존, 교체 중단")
    os.replace(name, path)
finally:
    if os.path.exists(name):
        os.unlink(name)
print("DEVELOP_PUBLIC_ENV_UPDATED=" + str(len(updates)))
print("BACKUP_DIR=" + str(backup))
print("EXISTING_DB_AND_SECRET_REFERENCE_PRESERVED")
print("NO_CONTAINER_RESTART_SECRET_DB_OR_STAGING_CHANGE")
'''


def build_request(payload):
    public = payload["public"]
    merge.inputs.validate_public({key: public[key] for key in merge.inputs.PUBLIC_KEYS})
    fixed = {"AI_CHAT_ENABLED": "true", "PAYMENT_SERVER_BASE_URL": "http://payment-server:8090",
             "CHAT_CONVERSATION_TOKEN_ISSUER": "seonjalal-v2-develop-chat", "PRODUCT_SEARCH_ENABLED": "false"}
    merge.require(set(public) == set(merge.inputs.PUBLIC_KEYS) | set(fixed), "공개 설정 허용키 불일치")
    merge.require(all(public[key] == value for key, value in fixed.items()), "승인된 공개 설정 불일치")
    # proposed_secrets는 SSM 요청에 포함하지 않음. 공개 키만 별도 선택.
    updates = {**public, **IMAGES}
    patch_env("".join(key + "=" + value + "\n" for key, value in OLD_IMAGES.items()), updates, OLD_IMAGES)
    prefix = runtime.REMOTE.split("files = __PUBLIC_FILES__", 1)[0]
    script = prefix + REMOTE.replace("__PATCH_FUNCTION__", inspect.getsource(patch_env)).replace(
        "__UPDATES__", repr(updates)).replace("__OLD_IMAGES__", repr(OLD_IMAGES)).replace("__IMAGES__", repr(IMAGES))
    compile(script, "<develop-env-update>", "exec")
    return {"DocumentName": "AWS-RunShellScript", "InstanceIds": [runtime.INSTANCE],
            "Comment": "Update approved Develop public env only; no restart",
            "TimeoutSeconds": 120, "Parameters": {"commands": ["python3 - <<'PY'\n" + script + "\nPY"],
                                                    "executionTimeout": ["120"]}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True)
    args = parser.parse_args()
    directory = Path(args.directory)
    merge.require(directory.resolve().parent == Path("/private/tmp") and
                  directory.name.startswith("seonjalal-develop-recovery."), "보호 입력 경로 불일치")
    merge.protected(directory, directory=True)
    merge.protected(directory / "inputs.json")
    request = build_request(json.loads((directory / "inputs.json").read_text()))
    os.umask(0o077)
    target = Path(tempfile.mkdtemp(prefix="seonjalal-develop-env-update.", dir="/private/tmp")) / "request.json"
    target.write_text(json.dumps(request))
    print("SSM_REQUEST_FILE=" + str(target))
    print("PUBLIC_ENV_KEYS=13")
    print("LOCAL_PREPARE_ONLY_NO_AWS_OR_SERVER_CHANGE")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("STOP: " + (str(error) if type(error) is ValueError else type(error).__name__))
        raise SystemExit(1)
