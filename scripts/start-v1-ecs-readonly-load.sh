#!/usr/bin/env bash
# Mac에서 SSM 시험 진입점만 대상으로 Smoke 또는 5분·1rps 읽기 기록.
set -euo pipefail
set +x
umask 077
task_repo_dir="$(cd "$(dirname "$0")/.." && pwd)"
cd "$task_repo_dir"
[[ "$(uname -s)" == Darwin ]] || { echo 'STOP: Mac에서 실행하세요.' >&2; exit 1; }
task_profile="${1:-smoke}"
[[ $# -le 1 && ( "$task_profile" == smoke || "$task_profile" == baseline ) ]] || { echo 'STOP: smoke 또는 baseline 필요.' >&2; exit 1; }
pgrep -x k6 >/dev/null && { echo 'STOP: 실행 중인 k6가 있습니다.' >&2; exit 1; }
command -v k6 >/dev/null
export BASE_URL='http://127.0.0.1:18084'
export ECS_RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-v1-ecs-readonly-${task_profile}-$$"
unset ACCESS_TOKEN ACCESS_TOKENS ACCESS_TOKENS_FILE JWT_SECRET_BASE64
# 부하 전 응답 표식 확인. 성공은 읽기 부하 합격을 의미하지 않음.
task_headers=$(curl --noproxy '*' -fsS --connect-timeout 5 --max-time 10 -D - -o /dev/null "${BASE_URL}/actuator/health")
printf '%s\n' "$task_headers" | tr -d '\r' | LC_ALL=C grep -Eiq '^X-Rehearsal-Upstream: (v1|ecs)$' || { echo 'STOP: 시험 진입점 표식 없음.' >&2; exit 1; }
if [[ "$task_profile" == baseline ]]; then
  printf '%s\n' "$task_headers" | tr -d '\r' | LC_ALL=C grep -Eiq '^X-Rehearsal-Upstream: v1$' || { echo 'STOP: 최초 지속 시험 대상은 v1이어야 합니다.' >&2; exit 1; }
fi
task_log="/private/tmp/${ECS_RUN_ID}.log"
(set -o noclobber; : > "$task_log")
printf 'RUN_ID=%s\nREQUEST_LOG=%s\n' "$ECS_RUN_ID" "$task_log"
if [[ "$task_profile" == smoke ]]; then
  set +e
  bash scripts/run-k6-with-artifacts.sh "$ECS_RUN_ID" smoke \
    load-tests/k6/workloads/v1-ecs-cutover-readonly.js > "$task_log" 2>&1
  task_exit=$?
  set -e
  tail -n 55 "$task_log"
  printf 'SMOKE_EXIT=%s\n' "$task_exit"
  exit "$task_exit"
fi
nohup bash scripts/run-k6-with-artifacts.sh "$ECS_RUN_ID" baseline \
  load-tests/k6/workloads/v1-ecs-cutover-readonly.js >> "$task_log" 2>&1 < /dev/null &
task_load_pid=$!
printf 'K6_PID=%s\n' "$task_load_pid"
sleep 2
tail -n 12 "$task_log"
kill -0 "$task_load_pid" 2>/dev/null || { echo 'STOP: 부하 종료. 로그 확인 필요.' >&2; exit 1; }
