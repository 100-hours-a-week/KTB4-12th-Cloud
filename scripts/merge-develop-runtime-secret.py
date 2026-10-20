#!/usr/bin/env python3
"""승인된 Develop 누락 Secret3개만 추가. 기존값 보존·버전 경합 차단·값 미출력."""
import argparse
import base64
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import uuid

spec = importlib.util.spec_from_file_location("inputs", Path(__file__).with_name("prepare-develop-recovery-inputs.py"))
inputs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inputs)
ACCOUNT = "721744297924"
SECRET = "/seonjalal/develop/runtime"
ADDED = {"PAYMENT_SERVER_API_KEY", "AI_CHAT_SERVICE_TOKEN", "CHAT_CONVERSATION_TOKEN_SECRET_BASE64"}
PRESERVED = ("DB_PASSWORD", "DB_ROOT_PASSWORD", "JWT_SECRET_BASE64", "LOGIN_RATE_LIMIT_HMAC_SECRET_BASE64", "PROFILING_SERVICE_TOKEN")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def aws_json(service, operation, *args, body=None):
    command = ["aws", "--profile", "seonjalal-production", "--region", "ap-northeast-2", "--no-cli-pager",
               "--cli-connect-timeout", "5", "--cli-read-timeout", "20", service, operation, *args, "--output", "json"]
    if body is not None:
        # 현재 Mac CLI의 /dev/stdin JSON 읽기 실패 우회. argv에는 경로만 전달.
        # 원문은 0700 임시 폴더의 0600 파일에 잠시 저장 후 성공/실패 모두 삭제.
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
        # 오류 원문에 입력 JSON이 포함될 수 있어 AWS 오류 식별자만 공개.
        match = re.search(r"An error occurred \(([A-Za-z0-9_.-]+)\)", result.stderr)
        code = match.group(1) if match else "CLI_OR_TRANSPORT_ERROR"
        raise ValueError("AWS 호출 실패: " + operation + " CODE=" + code +
                         ". 부분 반영 여부 확인 필요, 원문 비공개")
    return json.loads(result.stdout) if result.stdout.strip() else {}


def value_of(response):
    require(response.get("Name") == SECRET and bool(re.fullmatch(
        r"arn:aws:secretsmanager:ap-northeast-2:" + ACCOUNT + r":secret:/seonjalal/develop/runtime-[A-Za-z0-9]{6}",
        response.get("ARN", ""))), "Secret 대상 불일치")
    value = json.loads(response["SecretString"])
    require(isinstance(value, dict), "기존 Secret JSON 객체 확인 필요")
    return value


def merge_values(existing, proposed):
    require(set(proposed) == ADDED, "승인된 추가 Secret3개와 불일치")
    require(all(isinstance(existing.get(key), str) and existing[key].strip() for key in PRESERVED),
            "기존 DB/JWT 필수값 확인 필요")
    require(all(not existing.get(key) for key in ADDED), "추가 대상에 기존값 존재. 덮어쓰기 중단")
    require(all(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_./+=:@%-]+", value)
                for value in proposed.values()), "추가 Secret 형식 확인 필요")
    require(len(base64.b64decode(proposed["CHAT_CONVERSATION_TOKEN_SECRET_BASE64"], validate=True)) >= 32,
            "대화 서명키 길이 확인 필요")
    require(len(proposed["PAYMENT_SERVER_API_KEY"]) >= 32, "결제 내부키 길이 확인 필요")
    return {**existing, **proposed}


def perform(proposed, journal, api):
    current = api("secretsmanager", "get-secret-value", "--secret-id", SECRET, "--version-stage", "AWSCURRENT")
    # 통신 중단 뒤 재시도해도 같은 journal의 성공 결과를 읽기 검증만 수행.
    if current["VersionId"] == journal["new_version"]:
        previous = api("secretsmanager", "get-secret-value", "--secret-id", SECRET, "--version-id", journal["old_version"])
        expected = merge_values(value_of(previous), proposed)
        require(value_of(current) == expected, "이미 적용된 새 버전 내용 불일치")
        return
    require(current["VersionId"] == journal["old_version"], "기존 Secret 버전 변경 감지. 추가 중단")
    existing = value_of(current)
    merged = merge_values(existing, proposed)
    # 후보 버전을 먼저 검증. AWSCURRENT는 이 단계에서 이동하지 않음.
    api("secretsmanager", "put-secret-value", body={
        "SecretId": SECRET, "ClientRequestToken": journal["new_version"],
        "VersionStages": ["DEVELOP-RECOVERY-" + journal["new_version"]],
        "SecretString": json.dumps(merged),
    })
    candidate = api("secretsmanager", "get-secret-value", "--secret-id", SECRET, "--version-id", journal["new_version"])
    require(value_of(candidate) == merged, "후보 Secret 기존값 보존 검사 실패")
    latest = api("secretsmanager", "get-secret-value", "--secret-id", SECRET, "--version-stage", "AWSCURRENT")
    require(latest["VersionId"] == journal["old_version"] and value_of(latest) == existing,
            "후보 검증 중 기존 버전 변경 감지. 승격 중단")
    # 현재 버전이 바뀌었다면 RemoveFromVersionId 조건으로 승격 거부.
    api("secretsmanager", "update-secret-version-stage", "--secret-id", SECRET, "--version-stage", "AWSCURRENT",
        "--move-to-version-id", journal["new_version"], "--remove-from-version-id", journal["old_version"])
    final = api("secretsmanager", "get-secret-value", "--secret-id", SECRET, "--version-stage", "AWSCURRENT")
    require(final["VersionId"] == journal["new_version"] and value_of(final) == merged,
            "승격 후 검증 실패. 추가 쓰기 없이 현재 버전 확인 필요")


def protected(path, directory=False):
    require(not path.is_symlink(), "보호 경로 심볼릭링크 금지")
    info = path.stat()
    require(info.st_uid == os.getuid() and stat.S_IMODE(info.st_mode) == (0o700 if directory else 0o600),
            "보호 파일 소유자/권한 불일치")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--approved-secret-merge", action="store_true")
    args = parser.parse_args()
    require(args.approved_secret_merge, "누락 Secret3개 추가 승인 옵션 필요")
    os.umask(0o077)
    directory = Path(args.directory)
    require(directory.resolve().parent == Path("/private/tmp") and directory.name.startswith("seonjalal-develop-recovery."),
            "보호 입력 폴더 경계 불일치")
    protected(directory, directory=True)
    path = directory / "inputs.json"
    protected(path)
    payload = json.loads(path.read_text())
    inputs.validate_public({key: payload["public"][key] for key in inputs.PUBLIC_KEYS})
    require(payload["public"].get("PRODUCT_SEARCH_ENABLED") == "false", "ES 비활성 유지 필요")
    require(str(uuid.UUID(args.expected_version)) == args.expected_version, "기존 VersionId 형식 불일치")
    with os.fdopen(os.open(directory / ".merge.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        journal_path = directory / "secret-merge-record.json"
        if journal_path.exists():
            protected(journal_path)
            journal = json.loads(journal_path.read_text())
            require(journal.get("old_version") == args.expected_version and journal.get("secret") == SECRET,
                    "기존 변경 기록 대상 불일치")
            require(str(uuid.UUID(journal["new_version"])) == journal["new_version"], "새 버전 기록 형식 불일치")
        else:
            journal = {"secret": SECRET, "old_version": args.expected_version, "new_version": str(uuid.uuid4())}
            descriptor = os.open(journal_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w") as stream:
                json.dump(journal, stream)
                stream.flush()
                os.fsync(stream.fileno())
        identity = aws_json("sts", "get-caller-identity")
        require(identity.get("Account") == ACCOUNT, "대상 계정 불일치")
        perform(payload["proposed_secrets"], journal, aws_json)
    print("DEVELOP_SECRET_MERGE_VERIFIED")
    print("PREVIOUS_VERSION=" + journal["old_version"])
    print("CURRENT_VERSION=" + journal["new_version"])
    print("ADDED_KEYS=" + ",".join(sorted(ADDED)))
    print("EXISTING_DB_JWT_AND_OTHER_VALUES_PRESERVED")
    print("NO_CONTAINER_RESTART_OR_DB_CHANGE_AI_INTEGRATION_UNVERIFIED")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        # SDK/CLI/JSON 오류에는 비밀이 섞일 수 있으므로 정해진 검사 메시지만 공개.
        message = str(error) if type(error) is ValueError else type(error).__name__
        print("STOP: " + message, file=sys.stderr)
        sys.exit(1)
