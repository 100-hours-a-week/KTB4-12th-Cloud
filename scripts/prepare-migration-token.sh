#!/usr/bin/env bash
set -euo pipefail
set +x
umask 077

# 이번 리허설의 고정 발신자 계정 하나만 로그인한다. 기존 토큰 Pool은 보존한다.
if [[ "${BASE_URL:-}" != 'https://v1-benchmark.seonjalal.com' ]]; then
  printf 'BASE_URL must be https://v1-benchmark.seonjalal.com.\n' >&2
  exit 1
fi
command -v curl >/dev/null
command -v jq >/dev/null
task_repo_dir="$(cd "$(dirname "$0")/.." && pwd)"
task_token_dir="${task_repo_dir}/load-tests/secrets"
install -d -m 700 "$task_token_dir"
task_work_dir="$(mktemp -d "${task_token_dir}/migration-login.XXXXXX")"
task_output_file=''
task_keep_output=0

cleanup() {
  unset task_login_password
  # 이번 실행이 만든 임시 파일만 제거하고 기존 토큰 파일은 보존한다.
  rm -f "${task_work_dir}/request.json" "${task_work_dir}/response.json"
  rmdir "$task_work_dir"
  if [[ "$task_keep_output" != 1 && -n "$task_output_file" ]]; then
    rm -f "$task_output_file"
  fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

printf '기존 loadtest001 테스트 계정 비밀번호 입력: ' >&2
IFS= read -r -s task_login_password
printf '\n' >&2
[[ -n "$task_login_password" ]] || { printf 'Empty password.\n' >&2; exit 1; }

# 비밀번호를 인수/출력에 넣지 않고 보호된 임시 요청 파일에만 기록한다.
TASK_LOGIN_PASSWORD="$task_login_password" jq -n \
  '{email:"loadtest001@seonjalal.invalid", password:env.TASK_LOGIN_PASSWORD}' \
  > "${task_work_dir}/request.json"
unset task_login_password

task_status="$(curl --silent --show-error --connect-timeout 10 --max-time 30 \
  --output "${task_work_dir}/response.json" --write-out '%{http_code}' \
  --header 'Content-Type: application/json' \
  --data-binary "@${task_work_dir}/request.json" \
  "${BASE_URL}/api/auth/login")"
if [[ "$task_status" != 200 ]]; then
  printf 'LOGIN_FAILED HTTP=%s — 자동 재시도 없음\n' "$task_status" >&2
  exit 1
fi
if ! jq -e '.error == null and (.data.accessToken | type == "string" and length > 0)' \
  "${task_work_dir}/response.json" >/dev/null; then
  printf 'LOGIN_RESPONSE_INVALID — 응답 본문 공유 금지\n' >&2
  exit 1
fi

task_output_file="$(mktemp "${task_token_dir}/migration-access-token.XXXXXX")"
jq '[.data.accessToken]' "${task_work_dir}/response.json" > "$task_output_file"
task_keep_output=1
printf 'LOGIN_OK\n'
jq -r '"expires_in_seconds=" + ((.data.expiresIn // "unknown") | tostring)' \
  "${task_work_dir}/response.json"
printf 'TOKEN_FILE=%s\n' "$task_output_file"
