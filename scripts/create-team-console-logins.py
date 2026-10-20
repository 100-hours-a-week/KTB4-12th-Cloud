#!/usr/bin/env python3
"""승인한 신규4명의 콘솔 로그인만 생성. 기존 비밀번호 변경·Access Key 발급 없음."""
import argparse
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile

ACCOUNT = "721744297924"
USERS = ("fs-neo", "fs-ava", "ai-dylan", "ai-emet")


class AwsFailure(Exception):
    def __init__(self, operation, code):
        self.operation, self.code = operation, code
        super().__init__(operation + " CODE=" + code)


def api(service, operation, *args, body=None):
    command = ["aws", "--profile", "seonjalal-production", "--region", "ap-northeast-2", "--no-cli-pager",
               "--cli-connect-timeout", "5", "--cli-read-timeout", "20", service, operation, *args, "--output", "json"]
    if body is None:
        result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    else:
        # 비밀번호는 argv/stdin/SSM/State에 넣지 않고 보호 파일로 잠시 전달.
        with tempfile.TemporaryDirectory(prefix="seonjalal-login-request.", dir="/private/tmp") as temporary:
            path = Path(temporary) / "request.json"
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "w") as stream:
                json.dump(body, stream)
            result = subprocess.run(command + ["--cli-input-json", "file://" + str(path)],
                                    capture_output=True, text=True, timeout=60)
    if result.returncode:
        match = re.search(r"An error occurred \(([A-Za-z0-9_.-]+)\)", result.stderr)
        raise AwsFailure(operation, match[1] if match else "CLI_OR_TRANSPORT_ERROR")
    return json.loads(result.stdout) if result.stdout.strip() else {}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def profile(user, call):
    try:
        return call("iam", "get-login-profile", "--user-name", user)["LoginProfile"]
    except AwsFailure as error:
        if error.code != "NoSuchEntity":
            raise
        return None


def preflight(call):
    require(call("sts", "get-caller-identity").get("Account") == ACCOUNT, "대상 계정 불일치")
    try:
        policy = call("iam", "get-account-password-policy")["PasswordPolicy"]
    except AwsFailure as error:
        if error.code != "NoSuchEntity":
            raise
        policy = {}
    length = max(32, int(policy.get("MinimumPasswordLength", 8)))
    require(length <= 128, "계정 비밀번호 길이 정책 확인 필요")
    for user in USERS:
        identity = call("iam", "get-user", "--user-name", user)["User"]
        require(identity.get("Arn") == f"arn:aws:iam::{ACCOUNT}:user/{user}", "신규 사용자 대상 불일치")
        require(profile(user, call) is None, "기존 콘솔 로그인 발견. 비밀번호 덮어쓰기 없이 중단")
    return length


def password(length):
    # 모든 문자종류를 포함하며 초기 비밀번호는 충분한 난수로 생성.
    return "Aa1!" + "".join(secrets.choice("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_!@#%")
                           for _ in range(length - 4))


def save(path, record):
    # API 호출 전에 먼저 로컬 보존. 부분 성공 시 발급한 비밀번호 유실 방지.
    temporary = path.with_name(".credentials-writing.json")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(record, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def issue(call, directory):
    length = preflight(call)
    path = directory / "credentials.json"
    require(not path.exists(), "기존 인계 파일 존재. 자동 덮어쓰기 중단")
    record = {"account": ACCOUNT, "console_url": f"https://{ACCOUNT}.signin.aws.amazon.com/console/",
              "instructions": "담당자에게 본인 항목만 개별 전달. 첫 로그인 비밀번호 변경 후 MFA 등록. 전체 파일 공유 금지.",
              "users": {user: {"temporary_password": password(length), "status": "prepared",
                               "password_reset_required": True} for user in USERS}}
    save(path, record)
    print("PRIVATE_HANDOFF_FILE=" + str(path), flush=True)
    print("DO_NOT_PASTE_OR_SHARE_THE_WHOLE_FILE", flush=True)
    for user in USERS:
        require(profile(user, call) is None, "발급 직전 기존 로그인 발견. 추가 생성 중단")
        record["users"][user]["status"] = "pending"
        save(path, record)
        call("iam", "create-login-profile", body={"UserName": user,
             "Password": record["users"][user]["temporary_password"], "PasswordResetRequired": True})
        checked = profile(user, call)
        require(checked and checked.get("UserName") == user and checked.get("PasswordResetRequired") is True,
                "생성 후 로그인 정책 확인 실패. 추가 발급 중단")
        record["users"][user]["status"] = "issued"
        save(path, record)
        print("CONSOLE_LOGIN_CREATED=" + user, flush=True)
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approved-console-logins", action="store_true")
    args = parser.parse_args()
    require(args.approved_console_logins, "신규4명 콘솔 로그인 발급 승인 옵션 필요")
    os.umask(0o077)
    directory = Path(tempfile.mkdtemp(prefix="seonjalal-team-logins.", dir="/private/tmp"))
    print("PRIVATE_HANDOFF_DIR=" + str(directory), flush=True)
    try:
        issue(api, directory)
    except Exception:
        print("STOP: 부분 발급 여부는 보호 인계 파일로 확인. 자동 재실행/비밀번호 재설정 금지", file=sys.stderr)
        raise
    print("NEW_TEAM_CONSOLE_LOGINS_VERIFIED=4")
    print("PASSWORD_RESET_REQUIRED_MFA_BEFORE_SETTINGS_ACCESS")
    print("THEO_UNCHANGED_NO_ACCESS_KEYS_NO_RUNTIME_CHANGE")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        if isinstance(error, AwsFailure):
            message = str(error)
        elif type(error) is ValueError:
            message = str(error)
        else:
            message = type(error).__name__
        print("STOP: " + message, file=sys.stderr)
        raise SystemExit(1)
