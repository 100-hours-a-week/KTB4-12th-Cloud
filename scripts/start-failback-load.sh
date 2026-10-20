#!/usr/bin/env bash
# Mac의 다중행 붙여넣기를 피하기 위한 리허설 전용 부하 시작점.
set -euo pipefail
set +x
umask 077

task_repo_dir="$(cd "$(dirname "$0")/.." && pwd)"
cd "$task_repo_dir"
if [[ "$(uname -s)" != Darwin ]]; then
  printf 'STOP: Mac에서만 실행하세요.\n' >&2
  exit 1
fi
if pgrep -x k6 >/dev/null; then
  printf 'STOP: 기존 k6가 실행 중입니다. 중복 시작하지 않습니다.\n' >&2
  exit 1
fi
command -v k6 >/dev/null
command -v node >/dev/null

# 기존 토큰 파일을 재사용하되 실제 토큰은 출력하지 않음.
: "${ACCESS_TOKENS_FILE:?STOP: ACCESS_TOKENS_FILE 설정 필요}"
export BASE_URL='https://v1-benchmark.seonjalal.com'
export PRODUCT_ID=4 EXPECTED_UNIT_PRICE=22900 RECIPIENT_USER_ID=577
export ALLOW_REHEARSAL_WRITES=1 MIGRATION_EXPECT_WRITE_FREEZE=1 MIGRATION_INCLUDE_LOGIN=0
unset JWT_SECRET_BASE64
node -e '
const fs = require("node:fs");
const tokens = JSON.parse(fs.readFileSync(process.env.ACCESS_TOKENS_FILE, "utf8"));
const payload = JSON.parse(Buffer.from(tokens[0].split(".")[1], "base64url").toString());
const remaining = payload.exp - Math.floor(Date.now() / 1000);
if (!Number.isFinite(remaining) || remaining < 900) {
  console.error("STOP: 토큰 갱신 필요"); process.exit(1);
}
console.log("TOKEN_REMAINING_SECONDS=" + remaining);
'

# 기존 실행 ID/로그를 재사용하지 않고 새 시험으로 시작.
export MIGRATION_RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-failback-live"
task_request_log="/private/tmp/${MIGRATION_RUN_ID}.log"
(set -o noclobber; : > "$task_request_log")
nohup bash ./scripts/run-k6-with-artifacts.sh \
  "$MIGRATION_RUN_ID" baseline load-tests/k6/workloads/migration-readonly-cutover.js \
  >> "$task_request_log" 2>&1 < /dev/null &
task_load_pid=$!
printf 'K6_PID=%s\nRUN_ID=%s\nREQUEST_LOG=%s\n' \
  "$task_load_pid" "$MIGRATION_RUN_ID" "$task_request_log"

# 즉시 실패했다면 성공한 부하 시작으로 보고하지 않음.
sleep 2
tail -n 15 "$task_request_log"
if ! kill -0 "$task_load_pid" 2>/dev/null; then
  printf 'STOP: 부하 시작 프로세스가 종료됐습니다. 로그 확인 후 공유하세요.\n' >&2
  exit 1
fi
