#!/usr/bin/env python3
"""확인된 V1/V2 시험계정으로 로그인 1회. 보호된 Mac Fixture만 생성하며 선물/가입은 하지 않는다."""
import argparse
import base64
from datetime import datetime, timezone
import getpass
import json
import os
from pathlib import Path
import secrets
import stat
import subprocess
import tempfile
import time

BASE_URL = "https://v1-benchmark.seonjalal.com"
V2_BASE_URL = "https://seonjalal-v2-develop-runtime.tail234485.ts.net:8443"
EMAIL = "loadtest001@seonjalal.invalid"
SENDER = 524
RECIPIENT = 577


class SafetyStop(Exception):
    pass


def v2_baseline(record, now):
    if (record.get("account") != "721744297924"
            or record.get("db") != "seonjalal-v2-migration-rehearsal"
            or record.get("schema") != "seonjalal_develop"
            or (record.get("sender_id"), record.get("recipient_id"), record.get("product_id"),
                record.get("quantity"), record.get("expected_unit_price")) != (524, 577, 4, 1, 22900)):
        raise SafetyStop("V2 읽기 조회 계획의 대상/계정/상품 계약 불일치")
    try:
        prepared = datetime.fromisoformat(record["prepared_utc"])
        age = now - prepared.timestamp()
        if prepared.tzinfo is None or not 0 <= age <= 900:
            raise ValueError()
    except (ValueError, KeyError, TypeError):
        raise SafetyStop("V2 기준 조회가 15분 이내인지 확인 필요") from None
    stock, count, latest = (record.get("before_stock"), record.get("before_gift_count"),
                            record.get("before_latest_gift_id"))
    if (not all(type(value) is int and value >= 0 for value in (stock, count, latest))
            or stock < 31 or record.get("max_new_gifts", 0) < 30):
        raise SafetyStop("V2 재고/원장 기준 또는 smoke+baseline 예산 부족")
    return {"product4Stock": stock, "giftCount": count, "latestGiftId": latest}


def login_data(payload, now):
    data = payload.get("data", {})
    user = data.get("user", {})
    if payload.get("error") is not None or user.get("userId") != SENDER or user.get("email") != EMAIL:
        raise SafetyStop("로그인 응답의 시험 계정 ID/이메일 불일치")
    token = data.get("accessToken", "")
    try:
        parts = token.split(".")
        if len(parts) != 3:
            raise ValueError()
        claims = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)))
        expiration = claims["exp"]
        if (str(claims.get("sub")) != str(SENDER)
                or not isinstance(expiration, (int, float)) or expiration < now + 600):
            raise ValueError()
    except (ValueError, KeyError, TypeError, UnicodeError):
        raise SafetyStop("실제 로그인 토큰의 대상/잔여 유효시간 확인 필요") from None
    # 서명 검증/직접발급이 아니라 HTTPS 로그인 응답의 ID·유효시간 검사다.
    return token, expiration


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approved-test-login", action="store_true")
    parser.add_argument("--confirmed-source-baseline", action="store_true")
    parser.add_argument("--target", choices=("v1", "v2"), default="v1")
    parser.add_argument("--fixture-plan")
    args = parser.parse_args()
    if not args.approved_test_login:
        raise SafetyStop("시험 로그인 1회에 대한 명시적 승인 필요")
    if args.target == "v1":
        if not args.confirmed_source_baseline or args.fixture_plan:
            raise SafetyStop("확인한 Source 기준에 대한 명시적 승인 필요")
        baseline = {"product4Stock": 44, "giftCount": 5065, "latestGiftId": 5065}
        base_url = BASE_URL
        basis = "User read-only Source SQL and V1 API source review; not re-queried by this helper"
    else:
        if not args.fixture_plan:
            raise SafetyStop("V2는 TLS 읽기 조회 계획 파일이 필요함")
        path = Path(args.fixture_plan)
        if path.is_symlink() or not path.is_absolute():
            raise SafetyStop("계획 파일의 절대 경로 필요; symlink 금지")
        info = path.stat()
        if (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077
                or info.st_size > 65536):
            raise SafetyStop("계획 파일 소유자/권한/크기 불일치")
        with path.open(encoding="utf-8") as stream:
            baseline = v2_baseline(json.load(stream), time.time())
        base_url = V2_BASE_URL
        basis = "TLS read-only RDS fixture plan and fixed V1 API contract; no data reset"
    os.umask(0o077)
    password = getpass.getpass("loadtest001 시험 계정 비밀번호(화면에 표시되지 않음): ")
    if not password:
        raise SafetyStop("비밀번호 미입력; 로그인하지 않음")
    body = json.dumps({"email": EMAIL, "password": password})
    result = subprocess.run([
        "curl", "--silent", "--show-error", "--connect-timeout", "5", "--max-time", "15",
        "--proto", "=https", "--request", "POST", "--header", "Content-Type: application/json",
        "--data-binary", "@-", "--write-out", "\n%{http_code}", base_url + "/api/auth/login",
    ], input=body, capture_output=True, text=True)
    if result.returncode:
        raise SafetyStop("로그인 연결 실패 CURL_EXIT=" + str(result.returncode) + "; 자동 재시도 없음")
    try:
        raw, status = result.stdout.rsplit("\n", 1)
        if status != "200":
            raise SafetyStop("로그인 HTTP=" + status + "; 원문 비공개·자동 재시도 없음")
        token, expiration = login_data(json.loads(raw), time.time())
    except (ValueError, KeyError, TypeError):
        raise SafetyStop("로그인 응답 형식 불일치; 원문 비공개") from None
    prepared = datetime.now(timezone.utc).isoformat()
    fixture = {
        "target": args.target, "preparedUtc": prepared,
        "verifiedBackendConfigDigest": "sha256:b93ca625008ea5eb57e600dc56e033a7ca45bce8842ac76b789eb0da6563e1f2",
        "friendshipsVerified": True, "stockVerified": True, "sideEffectsReviewed": True,
        "verificationBasis": basis,
        "actors": [{"email": EMAIL, "password": password, "accessToken": token,
                    "userId": SENDER, "recipientUserId": RECIPIENT}],
        "products": [{"productId": 4, "expectedUnitPrice": 22900}],
        "signupPassword": "Lt9!" + secrets.token_hex(16),
        "maxSignups": 10, "maxGiftAttempts": 30,
        "baseline": baseline,
        "tokenExpiresAt": expiration,
        "limitations": ["Single sender/product: not broad user-distribution capacity",
                        "No signup or gift executed; smoke consumes separate data before baseline"],
    }
    directory = Path(tempfile.mkdtemp(prefix="seonjalal-" + args.target + "-write-fixture-", dir="/private/tmp"))
    path = directory / "fixture.json"
    with path.open("x", encoding="utf-8") as stream:
        json.dump(fixture, stream)
    path.chmod(0o600)
    public = {"target": args.target, "preparedUtc": prepared, "senderId": SENDER,
              "recipientId": RECIPIENT, "productId": 4, "unitPrice": 22900,
              "beforeStock": baseline["product4Stock"], "beforeGiftCount": baseline["giftCount"],
              "actualLoginCount": 1, "signupCount": 0, "giftCount": 0}
    (directory / "preflight.json").write_text(json.dumps(public, indent=2), encoding="utf-8")
    print(args.target.upper() + "_REAL_LOGIN_HTTP=200")
    print("TEST_USER_ID_VERIFIED=524")
    print("TOKEN_REMAINING_SECONDS=" + str(int(expiration - time.time())))
    print("WRITE_FIXTURE_FILE=" + str(path))
    print("NO_TOKEN_OR_PASSWORD_OUTPUT_NO_SIGNUP_OR_GIFT")


if __name__ == "__main__":
    try:
        main()
    except SafetyStop as error:
        raise SystemExit("STOP: " + str(error)) from None
    except (EOFError, KeyboardInterrupt):
        raise SystemExit("STOP: 입력 취소; 자동 재시도 없음") from None
    except Exception as error:
        raise SystemExit("STOP: " + type(error).__name__ + "; 상세 원문 비공개") from None
