#!/usr/bin/env bash

set -euo pipefail

# Develop 전용 Test 계정에서 Light·Normal·Heavy 각 10명의 Access Token을 발급한다.
# 로그인 응답과 비밀번호 요청 파일은 임시 디렉터리에만 두고 종료 시 삭제한다.

BASE_URL="${BASE_URL:-https://v1-benchmark.seonjalal.com}"
OUTPUT_FILE="${1:-load-tests/secrets/develop-access-tokens.json}"
OUTPUT_DIR="$(dirname "${OUTPUT_FILE}")"

if [[ ! "${BASE_URL}" =~ ^https://v1-benchmark\.seonjalal\.com/?$ ]]; then
  echo "Token provisioning is allowed only for https://v1-benchmark.seonjalal.com." >&2
  exit 1
fi

command -v curl >/dev/null || {
  echo "curl is required." >&2
  exit 1
}
command -v jq >/dev/null || {
  echo "jq is required." >&2
  exit 1
}

umask 077
install -d -m 700 "${OUTPUT_DIR}"
WORK_DIR="$(mktemp -d "${OUTPUT_DIR}/token-pool.XXXXXX")"
OUTPUT_TEMP="${OUTPUT_FILE}.tmp"

cleanup() {
  unset LOADTEST_LOGIN_PASSWORD
  rm -rf "${WORK_DIR}"
  rm -f "${OUTPUT_TEMP}"
}

stop_on_signal() {
  local exit_code="$1"
  trap - EXIT INT TERM
  cleanup
  printf '\nToken provisioning interrupted; temporary Secret files were removed.\n' >&2
  exit "${exit_code}"
}

trap cleanup EXIT
trap 'stop_on_signal 130' INT
trap 'stop_on_signal 143' TERM

persona_for() {
  local account_number="$1"
  if (( account_number <= 250 )); then
    printf 'Light'
  elif (( account_number <= 450 )); then
    printf 'Normal'
  else
    printf 'Heavy'
  fi
}

# Persona 구간 전체를 대표하도록 각 구간에서 계정을 분산 선택한다.
ACCOUNT_NUMBERS=(
  1 26 51 76 101 126 151 176 201 226
  251 273 295 317 339 361 383 405 427 449
  451 456 461 466 471 476 481 486 491 496
)

printf 'Preparing 30 representative Develop account tokens.\n'
printf 'Test 계정 공통 비밀번호 입력 후 Enter: ' >&2
IFS= read -r -s LOADTEST_LOGIN_PASSWORD
printf '\n' >&2

if [[ -z "${LOADTEST_LOGIN_PASSWORD}" ]]; then
  echo "Password input is empty." >&2
  exit 1
fi

for account_number in "${ACCOUNT_NUMBERS[@]}"; do
  account_suffix="$(printf '%03d' "${account_number}")"
  login_email="loadtest${account_suffix}@seonjalal.invalid"
  request_file="${WORK_DIR}/request-${account_suffix}.json"
  response_file="${WORK_DIR}/response-${account_suffix}.json"

  # Secret을 명령행 인자로 전달하지 않고 해당 jq Process의 환경에서만 JSON으로 변환한다.
  LOADTEST_LOGIN_EMAIL="${login_email}" \
  LOADTEST_LOGIN_PASSWORD="${LOADTEST_LOGIN_PASSWORD}" \
    jq -n '{email: env.LOADTEST_LOGIN_EMAIL, password: env.LOADTEST_LOGIN_PASSWORD}' \
    > "${request_file}"

  while true; do
    http_status="$(
      curl -sS \
        --connect-timeout 10 \
        --max-time 30 \
        -o "${response_file}" \
        -w '%{http_code}' \
        -H 'Content-Type: application/json' \
        --data-binary "@${request_file}" \
        "${BASE_URL%/}/api/auth/login"
    )"

    if [[ "${http_status}" == "200" ]]; then
      jq -e '
        .error == null
        and (.data.accessToken | type == "string")
        and (.data.accessToken | length > 0)
        and (.data.expiresIn > 0)
      ' "${response_file}" >/dev/null
      printf 'token_ready account=Test%s persona=%s\n' \
        "${account_number}" "$(persona_for "${account_number}")"
      break
    fi

    if [[ "${http_status}" == "429" ]]; then
      printf 'rate_limited account=Test%s retry_after_seconds=7\n' "${account_number}"
      sleep 7
      continue
    fi

    echo "Login failed for Test${account_number}: HTTP ${http_status}" >&2
    jq '{message, error}' "${response_file}" >&2
    exit 1
  done

  # 평문 비밀번호가 포함된 요청 파일은 계정별 로그인 직후 제거한다.
  rm -f "${request_file}"
done

# 응답 파일은 계정 번호 순서이므로 Light 10명, Normal 10명, Heavy 10명으로 읽힌다.
# 세 Persona를 교차 배치해 실제 VU가 적어도 한 Persona에만 치우치지 않게 한다.
jq -s '
  [
    range(0; 10) as $offset
    | .[$offset].data.accessToken,
      .[$offset + 10].data.accessToken,
      .[$offset + 20].data.accessToken
  ]
' "${WORK_DIR}"/response-*.json > "${OUTPUT_TEMP}"
jq -e 'length == 30 and all(.[]; type == "string" and length > 0)' \
  "${OUTPUT_TEMP}" >/dev/null
chmod 600 "${OUTPUT_TEMP}"
mv "${OUTPUT_TEMP}" "${OUTPUT_FILE}"

printf 'token_pool=success token_count=30 output=%s\n' "${OUTPUT_FILE}"
