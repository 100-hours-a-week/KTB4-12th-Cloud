#!/usr/bin/env python3
"""승인된 OSIV 단일 변수 비교용 SSM 요청 생성. AWS 직접 실행 없음."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import tempfile

spec = importlib.util.spec_from_file_location(
    "pool_diagnostic", Path(__file__).with_name("prepare-develop-pool-diagnostic.py"))
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)

# 대상 확인·보호 파일·오류 비노출 함수는 이미 검증한 진단 도구와 공유.
REMOTE = base.REMOTE.split("\ndef main():", 1)[0] + r'''
OSIV = "SPRING_JPA_OPEN_IN_VIEW"
SOURCE = BASE / "pool-diagnostic-zb2mz3lp"

def main():
    identity()
    with open("/run/seonjalal-develop-pool-diagnostic.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        perform()

def perform():
    marker = BASE / "osiv-comparison-active.json"
    if marker.exists():
        raise RuntimeError("OSIV_COMPARISON_ALREADY_PREPARED_DO_NOT_REPEAT")
    previous_marker = json.loads((BASE / "pool-diagnostic-active.json").read_text())
    if previous_marker.get("directory") != str(SOURCE) or previous_marker.get("main_image") != IMAGE:
        raise RuntimeError("PREVIOUS_DIAGNOSTIC_MARKER_MISMATCH")
    source_path = SOURCE / "diagnostic.compose.json"
    config = json.loads(source_path.read_text())
    old = inspect(MAIN)
    labels = old["Config"].get("Labels") or {}
    if (not old["State"]["Running"] or old["Image"] != IMAGE
        or labels.get("com.docker.compose.project") != "seonjalal-v2-develop"
        or labels.get("com.docker.compose.project.config_files") != str(source_path)):
        raise RuntimeError("CURRENT_DIAGNOSTIC_CONTAINER_MISMATCH")
    old_env = envmap(old["Config"].get("Env"))
    if OSIV in old_env or old_env.get(KEY) != "20000":
        raise RuntimeError("CURRENT_DIAGNOSTIC_SETTINGS_MISMATCH")
    service = config["services"]["main-backend"]
    if service["image"] != IMAGE or service["environment"].get(KEY) != "20000":
        raise RuntimeError("BASELINE_CONFIG_IMAGE_OR_LEAK_MISMATCH")
    image_env = envmap(json.loads(run(["docker", "image", "inspect", IMAGE]))[0]["Config"].get("Env"))
    baseline_env = dict(image_env)
    baseline_env.update(service.get("environment", {}))
    if baseline_env != old_env:
        raise RuntimeError("BASELINE_ENV_DIFFERS_FROM_RUNNING")
    command = ["docker", "compose", "--project-directory", str(BASE),
               "-p", "seonjalal-v2-develop", "-f", str(source_path)]
    hash_output = run(command + ["config", "--hash", "main-backend"])
    if hash_output.split()[-1] != labels.get("com.docker.compose.config-hash"):
        raise RuntimeError("BASELINE_CONFIG_HASH_CHANGED")

    before = inventory()
    directory = Path(tempfile.mkdtemp(prefix="osiv-comparison-", dir=BASE))
    os.chmod(directory, 0o700)
    write_private(directory / "main-before-inspect.json", json.dumps(old))
    write_private(directory / "rollback.compose.json", json.dumps(config))
    service["environment"][OSIV] = "false"
    target = directory / "comparison.compose.json"
    write_private(target, json.dumps(config))
    candidate = ["docker", "compose", "--project-directory", str(BASE),
                 "-p", "seonjalal-v2-develop", "-f", str(target)]
    # 비밀 문자열의 Compose 재해석까지 검사한 뒤에만 실제 재생성.
    rendered = json.loads(run(candidate + ["config", "--format", "json"]))
    rendered_env = dict(image_env)
    rendered_env.update(rendered["services"]["main-backend"]["environment"])
    expected_env = dict(old_env, **{OSIV: "false"})
    if rendered_env != expected_env or rendered["services"]["main-backend"]["image"] != IMAGE:
        raise RuntimeError("CANDIDATE_NOT_SINGLE_SETTING_CHANGE")
    write_private(marker, json.dumps({"directory": str(directory), "main_image": IMAGE}))
    write_private(directory / "started-at.txt", datetime.datetime.now(datetime.timezone.utc).isoformat())
    print("PRIVATE_BACKUP_DIR=" + str(directory), flush=True)
    print("DO_NOT_SHARE_BACKUP_CONTENTS", flush=True)
    run(candidate + ["up", "--detach", "--no-deps", "--pull", "never", "--no-build",
                     "--force-recreate", "--wait", "--wait-timeout", "180", "main-backend"])
    current = inspect(MAIN)
    if current["Image"] != IMAGE or envmap(current["Config"].get("Env")) != expected_env:
        raise RuntimeError("POST_CHANGE_IMAGE_OR_ENV_MISMATCH")
    after = inventory()
    if {k:v for k,v in before.items() if k != MAIN} != {k:v for k,v in after.items() if k != MAIN}:
        raise RuntimeError("OTHER_CONTAINER_INVENTORY_CHANGED")
    run(["docker", "exec", FE, "nginx", "-t"])
    run(["docker", "exec", FE, "nginx", "-s", "reload"])
    run(["curl", "--fail", "--silent", "--show-error", "--connect-timeout", "3",
         "--max-time", "12", "--output", "/dev/null", "http://127.0.0.1:8080/api/actuator/health"])
    print("MAIN_SAME_IMAGE_OSIV_FALSE_LEAK_20000_VERIFIED")
    print("OTHER_CONTAINER_IDS_PRESERVED_FE_NGINX_RELOADED")
    print("LOCAL_PROXY_HEALTH_HTTP_200")
    print("LIMITATION=OSIV hypothesis not proven; SSE/pool return and API regression QA pending")

try:
    main()
except Exception as error:
    print("STOP=" + (str(error) if isinstance(error, RuntimeError) else type(error).__name__))
    print("DO_NOT_REPEAT_OR_RUN_FULL_DEPLOY; preserve backups and inspect partial state")
    raise SystemExit(1)
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approved-osiv-comparison", action="store_true", required=True)
    parser.parse_args()
    compile(REMOTE, "<remote>", "exec")
    directory = Path(tempfile.mkdtemp(prefix="seonjalal-develop-osiv-comparison-"))
    os.chmod(directory, 0o700)
    request = {
        "DocumentName": "AWS-RunShellScript",
        "InstanceIds": ["i-06f8ccdce3a1e9ff4"],
        "Comment": "Approved Develop same-image OSIV-only comparison",
        "TimeoutSeconds": 600,
        "Parameters": {"commands": ["python3 - <<'PY'\n" + REMOTE + "\nPY"], "executionTimeout": ["480"]},
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
