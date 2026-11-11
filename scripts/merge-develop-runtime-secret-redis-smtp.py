#!/usr/bin/env python3
"""Develop 런타임 Secret에 Redis·SMTP 키 3개만 추가. 기존값 보존·버전 경합 차단·값 미출력.

- REDIS_PASSWORD: 이 도구가 새로 생성한다. 사람이 값을 보거나 입력하지 않는다.
- SMTP_USERNAME, SMTP_PASSWORD: SES 비밀(seonjalal/shared/ses-smtp-local)에서 복사한다.
- 기본은 계획만 출력하고 AWS를 변경하지 않는다. 실제 반영은 --execute와 계획에서 출력한 VersionId가 필요하다.
"""
import argparse
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
import tempfile
import uuid

ACCOUNT = "721744297924"
REGION = "ap-northeast-2"
PROFILE = "seonjalal-production"
SECRET = "/seonjalal/develop/runtime"
SECRET_ARN = re.compile(r"arn:aws:secretsmanager:" + REGION + ":" + ACCOUNT + r":secret:/seonjalal/develop/runtime-[A-Za-z0-9]{6}")
SES_SECRET = "seonjalal/shared/ses-smtp-local"
SES_SECRET_ARN = re.compile(r"arn:aws:secretsmanager:" + REGION + ":" + ACCOUNT + r":secret:seonjalal/shared/ses-smtp-local-[A-Za-z0-9]{6}")
SES_SMTP_HOST = "email-smtp." + REGION + ".amazonaws.com"
ADDED = ("REDIS_PASSWORD", "SMTP_USERNAME", "SMTP_PASSWORD")
# 기존 값은 하나도 바꾸지 않고, 아래 필수값이 있는 정상 Secret에만 추가한다.
PRESERVED = ("DB_PASSWORD", "DB_ROOT_PASSWORD", "JWT_SECRET_BASE64", "LOGIN_RATE_LIMIT_HMAC_SECRET_BASE64",
             "PROFILING_SERVICE_TOKEN", "PAYMENT_SERVER_API_KEY")
# deploy.sh의 dotenv 안전 문자 검사와 같은 범위. 통과하지 못하면 배포가 중단되므로 미리 거절한다.
SAFE_VALUE = re.compile(r"^[A-Za-z0-9_./+=:@%-]+$")
STATE_DIRECTORY = Path("/private/tmp/seonjalal-develop-redis-smtp")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def aws_json(service, operation, *args, body=None):
    command = ["aws", "--profile", PROFILE, "--region", REGION, "--no-cli-pager",
               "--cli-connect-timeout", "5", "--cli-read-timeout", "20", service, operation, *args, "--output", "json"]
    if body is not None:
        # 비밀은 argv에 두지 않고 0700 임시 폴더의 0600 파일로 잠시 전달한 뒤 성공·실패 모두 삭제한다.
        with tempfile.TemporaryDirectory(prefix="seonjalal-secret-request.", dir="/private/tmp") as temporary:
            path = Path(temporary) / "request.json"
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w") as stream:
                json.dump(body, stream)
            result = subprocess.run(command + ["--cli-input-json", "file://" + str(path)],
                                    capture_output=True, text=True, timeout=60)
    else:
        result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    if result.returncode:
        # 오류 원문에 입력 JSON이 포함될 수 있어 AWS 오류 식별자만 공개한다.
        match = re.search(r"An error occurred \(([A-Za-z0-9_.-]+)\)", result.stderr)
        code = match.group(1) if match else "CLI_OR_TRANSPORT_ERROR"
        raise ValueError("AWS 호출 실패: " + operation + " CODE=" + code + ". 부분 반영 여부 확인 필요, 원문 비공개")
    return json.loads(result.stdout) if result.stdout.strip() else {}


def runtime_value(response):
    require(response.get("Name") == SECRET and bool(SECRET_ARN.fullmatch(response.get("ARN", ""))), "런타임 Secret 대상 불일치")
    value = json.loads(response["SecretString"])
    require(isinstance(value, dict), "기존 Secret JSON 객체 확인 필요")
    return value


def check_existing(existing):
    require(all(isinstance(existing.get(key), str) and existing[key].strip() for key in PRESERVED), "기존 필수값 확인 필요")
    present = [key for key in ADDED if existing.get(key)]
    require(not present, "추가 대상에 기존값 존재. 덮어쓰기 중단: " + ",".join(present))


def read_ses(api):
    response = api("secretsmanager", "get-secret-value", "--secret-id", SES_SECRET)
    require(response.get("Name") == SES_SECRET and bool(SES_SECRET_ARN.fullmatch(response.get("ARN", ""))), "SES Secret 대상 불일치")
    value = json.loads(response["SecretString"])
    require(isinstance(value, dict), "SES Secret JSON 객체 확인 필요")
    require(value.get("SMTP_HOST") == SES_SMTP_HOST, "SES SMTP 호스트 불일치")
    for key in ("SMTP_USERNAME", "SMTP_PASSWORD"):
        require(isinstance(value.get(key), str) and value[key] != "", "SES Secret에 " + key + " 없음")
    return {"SMTP_USERNAME": value["SMTP_USERNAME"], "SMTP_PASSWORD": value["SMTP_PASSWORD"]}


def validate_proposed(proposed):
    require(set(proposed) == set(ADDED), "승인된 추가 Secret 3개와 불일치")
    invalid = [key for key in ADDED if not isinstance(proposed[key], str) or not SAFE_VALUE.fullmatch(proposed[key])]
    require(not invalid, "dotenv에 안전하지 않은 형식: " + ",".join(invalid))
    require(re.fullmatch(r"[0-9a-f]{64}", proposed["REDIS_PASSWORD"]) is not None, "REDIS_PASSWORD 형식 확인 필요")


def protected(path, directory=False):
    require(not path.is_symlink(), "보호 경로 심볼릭링크 금지")
    info = path.stat()
    require(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == (0o700 if directory else 0o600),
            "보호 파일 소유자/권한 불일치")


def ensure_state(state):
    os.umask(0o077)
    if state.exists():
        protected(state, directory=True)
    else:
        state.mkdir(mode=0o700)


def current_runtime(api):
    return api("secretsmanager", "get-secret-value", "--secret-id", SECRET, "--version-stage", "AWSCURRENT")


def plan(api):
    """AWS를 변경하지 않고 상태만 확인한다. 값은 출력하지 않는다."""
    identity = api("sts", "get-caller-identity")
    require(identity.get("Account") == ACCOUNT, "대상 계정 불일치")
    current = current_runtime(api)
    existing = runtime_value(current)
    check_existing(existing)
    read_ses(api)  # SES 비밀 형식과 호스트만 검증하고 값은 버린다.
    return current["VersionId"], sorted(existing)


def load_or_create_proposed(api, state):
    path = state / "proposed.json"
    if path.exists():
        protected(path)
        proposed = json.loads(path.read_text())
    else:
        proposed = {"REDIS_PASSWORD": secrets.token_hex(32), **read_ses(api)}
        validate_proposed(proposed)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            json.dump(proposed, stream)
            stream.flush()
            os.fsync(stream.fileno())
    validate_proposed(proposed)
    return proposed


def load_or_create_journal(state, expected_version):
    path = state / "journal.json"
    if path.exists():
        protected(path)
        journal = json.loads(path.read_text())
        require(journal.get("old_version") == expected_version and journal.get("secret") == SECRET, "기존 변경 기록 대상 불일치")
        require(str(uuid.UUID(journal["new_version"])) == journal["new_version"], "새 버전 기록 형식 불일치")
        return journal
    journal = {"secret": SECRET, "old_version": expected_version, "new_version": str(uuid.uuid4())}
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(journal, stream)
        stream.flush()
        os.fsync(stream.fileno())
    return journal


def apply(api, journal, proposed):
    current = current_runtime(api)
    # 통신 중단 뒤 재시도해도 이미 적용된 새 버전은 읽기 검증만 수행한다.
    if current["VersionId"] == journal["new_version"]:
        previous = api("secretsmanager", "get-secret-value", "--secret-id", SECRET, "--version-id", journal["old_version"])
        expected = {**runtime_value(previous), **proposed}
        require(runtime_value(current) == expected, "이미 적용된 새 버전 내용 불일치")
        return
    require(current["VersionId"] == journal["old_version"], "기존 Secret 버전 변경 감지. 추가 중단")
    existing = runtime_value(current)
    check_existing(existing)
    merged = {**existing, **proposed}
    # 후보 버전을 먼저 만들어 검증한다. AWSCURRENT는 이 단계에서 이동하지 않는다.
    api("secretsmanager", "put-secret-value", body={
        "SecretId": SECRET, "ClientRequestToken": journal["new_version"],
        "VersionStages": ["DEVELOP-REDIS-SMTP-" + journal["new_version"]],
        "SecretString": json.dumps(merged),
    })
    candidate = api("secretsmanager", "get-secret-value", "--secret-id", SECRET, "--version-id", journal["new_version"])
    require(runtime_value(candidate) == merged, "후보 Secret 기존값 보존 검사 실패")
    latest = current_runtime(api)
    require(latest["VersionId"] == journal["old_version"] and runtime_value(latest) == existing,
            "후보 검증 중 기존 버전 변경 감지. 승격 중단")
    # 현재 버전이 바뀌었다면 --remove-from-version-id 조건으로 승격이 거부된다.
    api("secretsmanager", "update-secret-version-stage", "--secret-id", SECRET, "--version-stage", "AWSCURRENT",
        "--move-to-version-id", journal["new_version"], "--remove-from-version-id", journal["old_version"])
    final = current_runtime(api)
    require(final["VersionId"] == journal["new_version"] and runtime_value(final) == merged,
            "승격 후 검증 실패. 추가 쓰기 없이 현재 버전 확인 필요")


def main(argv=None, api=aws_json, state=STATE_DIRECTORY):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="없으면 계획만 출력하고 AWS를 변경하지 않는다")
    parser.add_argument("--expected-version", help="계획 출력의 CURRENT_VERSION. --execute에 필요")
    args = parser.parse_args(argv)
    if not args.execute:
        version, keys = plan(api)
        print("PLAN_ONLY_NO_AWS_CHANGE")
        print("SECRET=" + SECRET)
        print("CURRENT_VERSION=" + version)
        print("EXISTING_KEYS=" + ",".join(keys))
        print("WILL_ADD=" + ",".join(ADDED))
        print("REDIS_PASSWORD_WILL_BE_GENERATED_AND_NEVER_DISPLAYED")
        print("SMTP_VALUES_COPIED_FROM=" + SES_SECRET + " (NEVER DISPLAYED)")
        print("EXECUTE_WITH: --execute --expected-version " + version)
        return 0
    require(args.expected_version is not None, "--expected-version 필요(계획 출력의 CURRENT_VERSION)")
    require(str(uuid.UUID(args.expected_version)) == args.expected_version, "VersionId 형식 불일치")
    identity = api("sts", "get-caller-identity")
    require(identity.get("Account") == ACCOUNT, "대상 계정 불일치")
    ensure_state(state)
    journal = load_or_create_journal(state, args.expected_version)
    # 이미 적용을 끝낸 뒤 다시 실행한 경우: 새 비밀번호를 만들지 않고 적용 사실만 확인한다.
    current = current_runtime(api)
    if current["VersionId"] == journal["new_version"] and not (state / "proposed.json").exists():
        present = runtime_value(current)
        require(all(present.get(key) for key in ADDED), "적용 기록은 있으나 추가 키가 없음. 현재 버전 확인 필요")
        print("ALREADY_APPLIED_NO_CHANGE")
        print("CURRENT_VERSION=" + journal["new_version"])
        return 0
    proposed = load_or_create_proposed(api, state)
    apply(api, journal, proposed)
    # 적용 검증이 끝나면 비밀이 담긴 입력 파일을 지운다. 기록(journal)에는 비밀이 없다.
    (state / "proposed.json").unlink(missing_ok=True)
    print("DEVELOP_RUNTIME_SECRET_MERGE_VERIFIED")
    print("PREVIOUS_VERSION=" + journal["old_version"])
    print("CURRENT_VERSION=" + journal["new_version"])
    print("ADDED_KEYS=" + ",".join(ADDED))
    print("EXISTING_VALUES_PRESERVED")
    print("NO_CONTAINER_RESTART_SERVER_FILES_NOT_UPDATED_YET")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as error:
        # SDK/CLI/JSON 오류에는 비밀이 섞일 수 있으므로 정해진 검사 메시지만 공개한다.
        message = str(error) if type(error) is ValueError else type(error).__name__
        print("STOP: " + message, file=sys.stderr)
        sys.exit(1)
