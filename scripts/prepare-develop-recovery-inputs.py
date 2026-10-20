#!/usr/bin/env python3
"""Develop 복구 입력만 보호 폴더에 준비. AWS·DB·서버 변경 없음."""

import argparse
import base64
import getpass
import json
import os
from pathlib import Path
import re
import secrets
import sys
import tempfile
from urllib.parse import urlsplit


PUBLIC_KEYS = (
    "AI_CHAT_BASE_URL", "AI_CHAT_CONNECT_TIMEOUT", "AI_CHAT_READ_TIMEOUT",
    "AI_CHAT_SSE_IDLE_TIMEOUT", "AI_CHAT_SSE_TOTAL_TIMEOUT",
    "AI_CHAT_SSE_MAX_CONNECTIONS",
)


def milliseconds(value):
    match = re.fullmatch(r"([1-9][0-9]*)(ms|s|m)", value)
    if not match:
        raise ValueError("시간은 양의 정수와 ms/s/m 단위를 사용해야 함")
    result = int(match[1]) * {"ms": 1, "s": 1000, "m": 60000}[match[2]]
    if result > 2147483647:
        raise ValueError("시간 범위 초과")
    return result


def validate_public(values):
    if set(values) != set(PUBLIC_KEYS):
        raise ValueError("AI 공개 설정 키 불일치")
    url = urlsplit(values["AI_CHAT_BASE_URL"])
    # 외부 AI에 서비스 토큰을 전달하므로 암호화된 주소만 준비한다.
    if (url.scheme != "https" or not url.hostname or url.username is not None
            or url.password is not None or url.query or url.fragment
            or re.search(r"\s", values["AI_CHAT_BASE_URL"])):
        raise ValueError("AI 주소는 인증정보·query·fragment 없는 HTTPS URL이어야 함")
    for key in PUBLIC_KEYS[1:5]:
        milliseconds(values[key])
    idle = milliseconds(values["AI_CHAT_SSE_IDLE_TIMEOUT"])
    total = milliseconds(values["AI_CHAT_SSE_TOTAL_TIMEOUT"])
    if total <= 180000 or total <= idle:
        raise ValueError("SSE total은 180초와 idle보다 커야 함")
    maximum = values["AI_CHAT_SSE_MAX_CONNECTIONS"]
    if not re.fullmatch(r"[1-9][0-9]*", maximum) or int(maximum) > 2147483647:
        raise ValueError("동시 연결 상한은 양의 int 범위여야 함")


def prepare_payload(values, ai_token):
    validate_public(values)
    if not re.fullmatch(r"[A-Za-z0-9_./+=:@%-]+", ai_token):
        raise ValueError("AI 토큰이 비어 있거나 현재 dotenv 전달 형식과 호환되지 않음")
    return {
        "public": {
            **values,
            "AI_CHAT_ENABLED": "true",
            "PAYMENT_SERVER_BASE_URL": "http://payment-server:8090",
            "CHAT_CONVERSATION_TOKEN_ISSUER": "seonjalal-v2-develop-chat",
            "PRODUCT_SEARCH_ENABLED": "false",
        },
        # 새 키 후보이며 기존 원격 Secret의 키 존재 여부 확인 없이 덮어쓰면 안 된다.
        "proposed_secrets": {
            "AI_CHAT_SERVICE_TOKEN": ai_token,
            "PAYMENT_SERVER_API_KEY": secrets.token_urlsafe(32),
            "CHAT_CONVERSATION_TOKEN_SECRET_BASE64": base64.b64encode(
                secrets.token_bytes(32)
            ).decode("ascii"),
        },
    }


def select_local_inputs(content):
    # 첨부 전체를 복사하지 않고 AI 공개설정6개와 서비스토큰1개만 선택.
    # DB/JWT/AWS키 및 그 밖의 값은 읽어도 결과에 포함하지 않음.
    allowed = set(PUBLIC_KEYS) | {"AI_CHAT_SERVICE_TOKEN"}
    selected = {}
    for line in content.splitlines():
        match = re.match(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$", line)
        if not match or match[1] not in allowed:
            continue
        key, value = match[1], match[2].strip()
        if key in selected:
            raise ValueError("AI 선택키 중복 입력")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in (chr(34), chr(39)):
            value = value[1:-1]
        selected[key] = value
    if set(selected) != allowed:
        raise ValueError("로컬 AI 선택 입력 누락")
    return {key: selected[key] for key in PUBLIC_KEYS}, selected["AI_CHAT_SERVICE_TOKEN"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--local-env-file")
    parser.add_argument("--acknowledge-local-test-ai", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    if args.local_env_file:
        if not args.acknowledge_local_test_ai:
            raise ValueError("로컬 시험 AI값이며 서버연동 미검증임을 명시적으로 확인해야 함")
        values, ai_token = select_local_inputs(Path(args.local_env_file).read_text())
    else:
        if not sys.stdin.isatty():
            raise ValueError("토큰 비노출을 위해 대화형 터미널에서 실행해야 함")
        print("확정된 Develop AI 설정 입력. 토큰은 화면에 표시하지 않음.")
        values = {key: input(key + " (확정값): ").strip() for key in PUBLIC_KEYS}
        ai_token = getpass.getpass("AI_CHAT_SERVICE_TOKEN (숨김 입력): ")
    payload = prepare_payload(values, ai_token)
    payload["ai_config_source"] = "local-test" if args.local_env_file else "interactive-input"
    payload["ai_integration_verified"] = False
    directory = Path(tempfile.mkdtemp(prefix="seonjalal-develop-recovery.", dir="/private/tmp"))
    target = directory / "inputs.json"
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(payload, stream)
        stream.flush()
        os.fsync(stream.fileno())
    print("RECOVERY_INPUT_DIR=" + str(directory))
    print("AI_PUBLIC_KEYS=6\nPROPOSED_SECRET_KEYS=3")
    print("AI_SERVER_INTEGRATION_NOT_VERIFIED")
    print("LOCAL_INPUTS_PREPARED_NO_AWS_OR_SERVER_CHANGE")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, EOFError, KeyboardInterrupt):
        raise SystemExit("STOP: 입력 형식 또는 필수값 확인 필요. 비밀값은 출력하지 않음.")
