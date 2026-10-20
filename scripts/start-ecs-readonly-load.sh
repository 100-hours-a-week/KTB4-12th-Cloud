#!/usr/bin/env bash
# Mac 전용: 한 줄 명령으로 Smoke 또는20분 순수 읽기 요청 시작. AWS 변경 없음.
set -euo pipefail
set +x
umask 077
task_repo_dir="$(cd "$(dirname "$0")/.." && pwd)"
cd "$task_repo_dir"
[[ "$(uname -s)" == Darwin ]] || { echo 'STOP: Mac에서만 실행하세요.' >&2; exit 1; }
task_profile="${1:-smoke}"
[[ $# -le 1 && ( "$task_profile" == smoke || "$task_profile" == baseline ) ]] || { echo 'STOP: smoke 또는 baseline만 지정하세요.' >&2; exit 1; }
pgrep -x k6 >/dev/null && { echo 'STOP: 실행 중인 k6가 있습니다.' >&2; exit 1; }
command -v k6 >/dev/null
export BASE_URL='https://v2-staging.seonjalal.com'
export ECS_RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-ecs-readonly-${task_profile}-$$"
unset ACCESS_TOKEN ACCESS_TOKENS ACCESS_TOKENS_FILE JWT_SECRET_BASE64
task_log="/private/tmp/${ECS_RUN_ID}.log"
(set -o noclobber; : > "$task_log")
printf 'RUN_ID=%s\nREQUEST_LOG=%s\n' "$ECS_RUN_ID" "$task_log"
if [[ "$task_profile" == smoke ]]; then
  set +e
  bash scripts/run-k6-with-artifacts.sh "$ECS_RUN_ID" smoke \
    load-tests/k6/workloads/ecs-deployment-readonly.js > "$task_log" 2>&1
  task_exit=$?
  set -e
  tail -n 55 "$task_log"
  printf 'SMOKE_EXIT=%s\n' "$task_exit"
  exit "$task_exit"
fi
nohup bash scripts/run-k6-with-artifacts.sh "$ECS_RUN_ID" baseline \
  load-tests/k6/workloads/ecs-deployment-readonly.js >> "$task_log" 2>&1 < /dev/null &
task_load_pid=$!
printf 'K6_PID=%s\n' "$task_load_pid"
sleep 2
tail -n 12 "$task_log"
kill -0 "$task_load_pid" 2>/dev/null || { echo 'STOP: 부하가 종료됐습니다. 로그를 확인하세요.' >&2; exit 1; }
