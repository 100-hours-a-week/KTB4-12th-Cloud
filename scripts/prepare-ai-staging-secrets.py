#!/usr/bin/env python3
"""스테이징 AI 앱 Secret(`seonjalal/staging/ai/runtime`)에 DB 주소 외의 값을 병합한다.

순서: 부트스트랩이 DB 주소 3개를 먼저 기록해야 한다(부트스트랩은 앱 Secret에 그 3개 외 키가 있으면 중단한다).
이 도구는 그 뒤에 실행해 기존 JSON에 키를 추가한다. 이미 있는 키는 `--replace`가 없으면 덮어쓰지 않는다.

  난수 생성: AI_INTERNAL_SERVICE_TOKEN, SESSION_SECRET, QA_ADMIN_PASSWORD
  숨김 입력: OPENROUTER_API_KEY_CHAT, OPENROUTER_API_KEY_PROFILER (형식 검증으로 붙여넣기 오류를 잡는다)
  선택:      BE_SERVICE_TOKEN(--be-token-from 으로 BE Secret에서 복사하거나 숨김 입력), DEMO_ACCESS_TOKEN(숨김 입력)

값은 화면·명령행·로그에 출력하지 않고 키 이름만 출력한다. 기본은 계획만 보여 주며 --execute가 있어야 쓴다.
"""
import argparse
import getpass
import json
import os
import re
import secrets
import subprocess
import sys
import tempfile

ACCOUNT_ID = "721744297924"
REGION = "ap-northeast-2"
PROFILE = "seonjalal-production"
SECRET_ID = "seonjalal/staging/ai/runtime"
BACKEND_SECRET_ID = "seonjalal/staging/backend/split-runtime"
DSN_KEYS = ("DATABASE_URL", "PROFILING_DATABASE_URL", "SEARCH_CATALOG_DATABASE_URL")
GENERATED = ("AI_INTERNAL_SERVICE_TOKEN", "SESSION_SECRET", "QA_ADMIN_PASSWORD")
PROVIDER_KEYS = ("OPENROUTER_API_KEY_CHAT", "OPENROUTER_API_KEY_PROFILER")
OPTIONAL = ("BE_SERVICE_TOKEN", "DEMO_ACCESS_TOKEN")
ALL_KEYS = GENERATED + PROVIDER_KEYS + OPTIONAL
BE_TOKEN_SOURCES = ("AI_CHAT_SERVICE_TOKEN", "PROFILING_SERVICE_TOKEN")
PROVIDER_PATTERN = re.compile(r"^sk-or-v1-[0-9a-f]{64}$")
TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9._~+/=-]{16,512}$")


class SecretsCli:
    def _run(self, *args):
        result = subprocess.run(["aws", *args, "--profile", PROFILE, "--region", REGION], capture_output=True, text=True)
        if result.returncode != 0:
            first = (result.stderr.strip().splitlines() or ["(no stderr)"])[0][:200]
            raise RuntimeError(f"aws {args[0]} {args[1]} 실패: {first}")
        return result.stdout

    def account(self):
        return json.loads(self._run("sts", "get-caller-identity", "--output", "json"))["Account"]

    def read(self, secret_id):
        return json.loads(self._run("secretsmanager", "get-secret-value", "--secret-id", secret_id, "--query", "SecretString", "--output", "text"))

    def write(self, secret_id, values):
        # 비밀은 명령행에 넣지 않고 권한 600 임시 파일로 전달한 뒤 즉시 삭제한다.
        with tempfile.NamedTemporaryFile("w", delete=False, prefix="ai-secret-", suffix=".json") as handle:
            os.chmod(handle.name, 0o600)
            json.dump(values, handle, separators=(",", ":"))
            path = handle.name
        try:
            self._run("secretsmanager", "put-secret-value", "--secret-id", secret_id, "--secret-string", f"file://{path}")
        finally:
            os.remove(path)


def require(condition, message):
    if not condition:
        raise SystemExit("STOP: " + message)


def valid(key, value):
    if key in PROVIDER_KEYS:
        return PROVIDER_PATTERN.fullmatch(value) is not None
    return TOKEN_PATTERN.fullmatch(value) is not None


def generated_value(key):
    return secrets.token_urlsafe(24 if key == "QA_ADMIN_PASSWORD" else 48)


def plan(existing, args):
    wanted = list(args.only) if args.only else [k for k in ALL_KEYS if k not in OPTIONAL]
    if args.be_token_from and "BE_SERVICE_TOKEN" not in wanted:
        wanted.append("BE_SERVICE_TOKEN")
    if args.prompt_optional:
        wanted += [k for k in args.prompt_optional if k not in wanted]
    wanted += [k for k in args.replace if k not in wanted]
    add, keep, replace = [], [], []
    for key in wanted:
        if key in existing and key not in args.replace:
            keep.append(key)
        else:
            (replace if key in existing else add).append(key)
    return add, keep, replace


def collect(add_keys, args, store, prompt):
    values = {}
    for key in add_keys:
        if key in GENERATED:
            values[key] = generated_value(key)
        elif key == "BE_SERVICE_TOKEN" and args.be_token_from:
            source = store.read(BACKEND_SECRET_ID).get(args.be_token_from)
            require(source and valid(key, source), f"BE Secret의 {args.be_token_from} 값을 쓸 수 없다")
            values[key] = source
        else:
            value = prompt(f"{key} 입력(화면에 표시되지 않음): ").strip()
            require(valid(key, value), f"{key} 형식이 올바르지 않다(붙여넣기 오류 확인)")
            values[key] = value
    return values


def main(argv=None, store=None, prompt=getpass.getpass, out=sys.stdout):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--execute", action="store_true", help="없으면 계획만 출력하고 Secret을 바꾸지 않는다")
    parser.add_argument("--only", action="append", choices=ALL_KEYS, help="이 키만 처리(여러 번 지정 가능)")
    parser.add_argument("--replace", action="append", default=[], choices=ALL_KEYS, help="이미 있는 키를 교체(회전). 지정하지 않으면 덮어쓰지 않음")
    parser.add_argument("--be-token-from", choices=BE_TOKEN_SOURCES, help="BE_SERVICE_TOKEN을 스테이징 BE Secret의 해당 키에서 복사")
    parser.add_argument("--prompt-optional", action="append", choices=OPTIONAL, help="선택 키(DEMO_ACCESS_TOKEN 등)를 숨김 입력으로 추가")
    args = parser.parse_args(argv)
    store = store or SecretsCli()
    require(store.account() == ACCOUNT_ID, "대상 계정이 다르다")
    existing = store.read(SECRET_ID)
    require(all(k in existing for k in DSN_KEYS), "DB 주소 키가 없다. 부트스트랩을 먼저 실행해야 한다")
    add, keep, replace = plan(existing, args)
    print(f"대상 Secret: {SECRET_ID}", file=out)
    print("추가:", ", ".join(add) or "(없음)", file=out)
    print("교체:", ", ".join(replace) or "(없음)", file=out)
    print("이미 있어 유지:", ", ".join(keep) or "(없음)", file=out)
    if not (add or replace):
        print("변경할 것이 없다", file=out)
        return {"added": [], "replaced": []}
    if not args.execute:
        prompts = [k for k in add + replace if k not in GENERATED and not (k == "BE_SERVICE_TOKEN" and args.be_token_from)]
        print("계획만 출력했다. 숨김 입력이 필요한 키:", ", ".join(prompts) or "(없음)", "/ 실제 실행은 --execute", file=out)
        return {"added": [], "replaced": []}
    new_values = collect(add + replace, args, store, prompt)
    merged = {**existing, **new_values}
    store.write(SECRET_ID, merged)
    verify = store.read(SECRET_ID)
    require(set(verify) == set(merged) and all(verify[k] == merged[k] for k in merged), "저장 후 검증에 실패했다")
    require(all(verify[k] == existing[k] for k in existing if k not in replace), "기존 값이 바뀌었다")
    print("완료: 키 이름만 표시:", ", ".join(sorted(new_values)), file=out)
    print("현재 Secret 키:", ", ".join(sorted(verify)), file=out)
    return {"added": add, "replaced": replace}


if __name__ == "__main__":
    try:
        main()
    except RuntimeError as error:
        print("실패:", error, file=sys.stderr)
        sys.exit(1)
