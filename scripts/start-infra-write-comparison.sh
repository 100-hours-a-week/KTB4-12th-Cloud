#!/usr/bin/env bash
# Mac local: 확인된 비운영 Fixture로만 실행. 실제 쓰기 시험이므로 먼저 smoke 승인을 받는다.
set -euo pipefail
: "${LOAD_TARGET:?v1 또는 v2 필요}"
: "${BASE_URL:?정확한 비운영 URL 필요}"
: "${PROFILE:?smoke 또는 baseline 필요}"
: "${LOAD_RUN_ID:?매번 새로운 run ID 필요}"
: "${WRITE_FIXTURE_FILE:?절대 경로의 보호된 Fixture 파일 필요}"
[[ "${ALLOW_COMPARISON_WRITES:-}" == 1 ]] || { echo 'STOP: 시험 쓰기 승인 필요' >&2; exit 1; }
[[ -z "${K6_HTTP_DEBUG:-}" && "${K6_INSECURE_SKIP_TLS_VERIFY:-false}" != true ]] || {
  echo 'STOP: Secret 노출 debug/TLS 우회 금지' >&2; exit 1;
}
[[ "$LOAD_TARGET" == v1 || "$LOAD_TARGET" == v2 ]]
[[ "$PROFILE" == smoke || "$PROFILE" == baseline ]]
[[ "$LOAD_RUN_ID" =~ ^[a-z0-9-]{8,48}$ ]]
[[ "$WRITE_FIXTURE_FILE" == /* && -f "$WRITE_FIXTURE_FILE" ]]
python3 - "$WRITE_FIXTURE_FILE" <<'PY'
import json, os, stat, sys, time
mode = stat.S_IMODE(os.stat(sys.argv[1]).st_mode)
if mode & 0o077:
    raise SystemExit('STOP: Fixture 파일은 그룹/다른 사용자 접근이 없어야 합니다(0600).')
with open(sys.argv[1], encoding='utf-8') as stream:
    fixture = json.load(stream)
if fixture.get('tokenExpiresAt', 0) < time.time() + 400:
    raise SystemExit('STOP: 토큰 잔여시간이 부족합니다. 새 로그인 후 실행하세요.')
PY
umask 077
result_dir="load-tests/results/${LOAD_RUN_ID}"
[[ ! -e "$result_dir" ]] || { echo 'STOP: 기존 결과 덮어쓰기 금지. 새 run ID 필요' >&2; exit 1; }
mkdir -p "$result_dir"
printf 'target=%s\nbase_url=%s\nprofile=%s\nrun_id=%s\n' \
  "$LOAD_TARGET" "$BASE_URL" "$PROFILE" "$LOAD_RUN_ID" > "$result_dir/metadata.txt"
date -u '+started_utc=%Y-%m-%dT%H:%M:%SZ' >> "$result_dir/metadata.txt"
shasum -a 256 load-tests/k6/workloads/infra-write-comparison.js \
  load-tests/k6/lib/write-load-contract.mjs >> "$result_dir/metadata.txt"
# Fixture 자체·계정비밀번호·토큰은 결과에 복사하지 않는다.
set +e
k6 run --console-output "$result_dir/request.log" \
  --summary-export "$result_dir/k6-summary.json" \
  --out "json=$result_dir/k6-metrics.json.gz" \
  load-tests/k6/workloads/infra-write-comparison.js
exit_code=$?
set -e
printf 'k6_exit_code=%s\n' "$exit_code" >> "$result_dir/metadata.txt"
date -u '+finished_utc=%Y-%m-%dT%H:%M:%SZ' >> "$result_dir/metadata.txt"
printf 'RESULT_DIR=%s\nK6_EXIT=%s\n' "$result_dir" "$exit_code"
exit "$exit_code"
