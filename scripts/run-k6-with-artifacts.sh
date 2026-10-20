#!/usr/bin/env bash

set -euo pipefail

if [[ $# -ne 3 ]]; then
  echo "Usage: $0 TEST_ID PROFILE SCRIPT" >&2
  exit 1
fi

TEST_ID="$1"
PROFILE="$2"
SCRIPT="$3"

if [[ ! "${TEST_ID}" =~ ^[0-9]{8}T[0-9]{6}Z-[a-z0-9-]+$ ]]; then
  echo "TEST_ID must look like 20261001T120000Z-products-baseline." >&2
  exit 1
fi

case "${PROFILE}" in
  smoke|baseline|capacity|spike|stress|breakpoint) ;;
  *)
    echo "Unsupported PROFILE: ${PROFILE}" >&2
    exit 1
    ;;
esac

if [[ ! -f "${SCRIPT}" || "${SCRIPT}" != load-tests/k6/*.js && "${SCRIPT}" != load-tests/k6/*/*.js ]]; then
  echo "SCRIPT must be a JavaScript file below load-tests/k6/." >&2
  exit 1
fi

: "${BASE_URL:?BASE_URL is required}"

# 고정된 스테이징 순수 읽기 시험만 무인증 허용. 기존 쓰기/인증 시험 조건 유지.
READONLY_ECS=false
if [[ "${SCRIPT}" == load-tests/k6/workloads/ecs-deployment-readonly.js ]]; then
  [[ "${BASE_URL}" == https://v2-staging.seonjalal.com ]] || { echo "STOP: staging URL required" >&2; exit 1; }
  READONLY_ECS=true
fi
# 별도 시험 프록시는 읽기 경로만 제공하며 외부 주소로 확장하지 않음.
if [[ "${SCRIPT}" == load-tests/k6/workloads/v1-ecs-cutover-readonly.js ]]; then
  [[ "${BASE_URL}" == http://127.0.0.1:18084 ]] || { echo "STOP: isolated loopback URL required" >&2; exit 1; }
  READONLY_ECS=true
fi
if [[ "${READONLY_ECS}" != true && -z "${ACCESS_TOKEN:-}" && -z "${ACCESS_TOKENS:-}" && -z "${ACCESS_TOKENS_FILE:-}" ]]; then
  echo "ACCESS_TOKEN, ACCESS_TOKENS, or ACCESS_TOKENS_FILE is required." >&2
  exit 1
fi

LOCAL_RUN_DIR="load-tests/results/${TEST_ID}"
install -d -m 700 "${LOCAL_RUN_DIR}"

if [[ "${PROFILE}" == "smoke" ]]; then
  # 0.2초 안에 끝나는 Smoke도 HTML Dashboard에 충분한 표본을 남깁니다.
  DASHBOARD_PERIOD="10ms"
else
  DASHBOARD_PERIOD="1s"
fi

{
  echo "test_id=${TEST_ID}"
  echo "profile=${PROFILE}"
  echo "script=${SCRIPT}"
  echo "base_url=${BASE_URL}"
  if [[ "${READONLY_ECS}" == true ]]; then
    echo "auth_mode=anonymous_readonly"
  elif [[ -n "${JWT_SECRET_BASE64:-}" ]]; then
    # Secret 값은 기록하지 않고 이번 실행의 인증 방식만 남긴다.
    echo "auth_mode=direct_jwt_from_seed_subjects"
  else
    echo "auth_mode=provided_access_tokens"
  fi
  echo "started_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "k6_version=$(k6 version | head -1)"
} > "${LOCAL_RUN_DIR}/metadata.txt"

set +e
env \
  PROFILE="${PROFILE}" \
  K6_WEB_DASHBOARD=true \
  K6_WEB_DASHBOARD_PORT=-1 \
  K6_WEB_DASHBOARD_PERIOD="${DASHBOARD_PERIOD}" \
  K6_WEB_DASHBOARD_EXPORT="${LOCAL_RUN_DIR}/k6-report.html" \
  k6 run \
    --summary-export "${LOCAL_RUN_DIR}/k6-summary.json" \
    --out "json=${LOCAL_RUN_DIR}/k6-metrics.json.gz" \
    "${SCRIPT}"
K6_EXIT_CODE=$?
set -e

{
  echo "finished_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "k6_exit_code=${K6_EXIT_CODE}"
} >> "${LOCAL_RUN_DIR}/metadata.txt"

echo "Artifacts: ${LOCAL_RUN_DIR}"
exit "${K6_EXIT_CODE}"
