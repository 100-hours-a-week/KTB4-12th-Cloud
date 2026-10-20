#!/usr/bin/env bash

set -euo pipefail

# 상품 카테고리는 이미 완료했으므로 나머지 V1 읽기·검증 API 8개의 Baseline을 순차 실행한다.
# 한 시험이 Threshold를 넘으면 즉시 중단해 뒤의 결과가 장애 상태와 섞이지 않게 한다.

required_variables=(
  BASE_URL
  ACCESS_TOKENS_FILE
  JWT_SECRET_BASE64
  JWT_ISSUER
  PRODUCT_ID
  RECEIVED_GIFT_ID
  RECIPIENT_USER_ID
  SEARCH_QUERY
  QUANTITY
)

for variable_name in "${required_variables[@]}"; do
  if [[ -z "${!variable_name:-}" ]]; then
    echo "${variable_name} is required." >&2
    exit 1
  fi
done

if [[ "${BASE_URL%/}" != "https://v1-benchmark.seonjalal.com" ]]; then
  echo "The V1 baseline suite is allowed only on https://v1-benchmark.seonjalal.com." >&2
  exit 1
fi

if [[ ! -f "${ACCESS_TOKENS_FILE}" ]]; then
  echo "ACCESS_TOKENS_FILE does not exist." >&2
  exit 1
fi

umask 077
SUITE_ID="${SUITE_ID:-$(date -u +%Y%m%dT%H%M%SZ)-v1-read-baselines}"
SUITE_DIR="load-tests/results/${SUITE_ID}"
MANIFEST="${SUITE_DIR}/suite-manifest.txt"
FULL_TOKEN_FILE="${ACCESS_TOKENS_FILE}"
# k6의 open()은 상대 경로를 실행 Script 기준으로 해석하므로 임시 파일도 절대 경로를 사용한다.
SINGLE_USER_TOKEN_FILE="$(mktemp "${PWD}/load-tests/secrets/single-user-token.XXXXXX")"
START_FROM="${START_FROM:-products-list}"

cleanup() {
  rm -f "${SINGLE_USER_TOKEN_FILE}"
  export ACCESS_TOKENS_FILE="${FULL_TOKEN_FILE}"
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# 고정 Gift·Friend Fixture는 Test1 소유이므로 첫 번째 Test1 Seed만 사용하는 파일을 임시 생성한다.
jq '[.[0]]' "${FULL_TOKEN_FILE}" > "${SINGLE_USER_TOKEN_FILE}"
jq -e 'length == 1 and (.[0] | type == "string" and length > 0)' \
  "${SINGLE_USER_TOKEN_FILE}" >/dev/null
chmod 600 "${SINGLE_USER_TOKEN_FILE}"

install -d -m 700 "${SUITE_DIR}"

tests=(
  'products-list:load-tests/k6/api/products-list.js'
  'product-detail:load-tests/k6/api/product-detail.js'
  'user-profile:load-tests/k6/api/user-profile.js'
  'friends-list:load-tests/k6/api/friends-list.js'
  'friends-search:load-tests/k6/api/friends-search.js'
  'received-gifts-list:load-tests/k6/api/received-gifts-list.js'
  'received-gift-detail:load-tests/k6/api/received-gift-detail.js'
  'gift-preflight:load-tests/k6/api/gift-preflight.js'
)

if [[ -f "${MANIFEST}" ]]; then
  {
    echo "resumed_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "resume_from=${START_FROM}"
  } >> "${MANIFEST}"
else
  {
    echo "suite_id=${SUITE_ID}"
    echo "profile=baseline"
    echo "started_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    echo "auth_mode=direct_jwt_from_seed_subjects"
  } > "${MANIFEST}"
fi

export PROFILE='baseline'
export GIFT_ID="${RECEIVED_GIFT_ID}"
start_found=false

for test_definition in "${tests[@]}"; do
  test_name="${test_definition%%:*}"
  test_script="${test_definition#*:}"

  if [[ "${test_name}" == "${START_FROM}" ]]; then
    start_found=true
  fi
  if [[ "${start_found}" == false ]]; then
    continue
  fi

  test_id="$(date -u +%Y%m%dT%H%M%SZ)-${test_name}-baseline"

  case "${test_name}" in
    friends-search|received-gift-detail|gift-preflight)
      # 이 세 Endpoint의 입력 ID는 Test1의 친구·선물 관계에 묶여 있다.
      export ACCESS_TOKENS_FILE="${SINGLE_USER_TOKEN_FILE}"
      ;;
    *)
      # 사용자별 목록과 Profile은 30명 Persona Pool을 순환 사용한다.
      export ACCESS_TOKENS_FILE="${FULL_TOKEN_FILE}"
      ;;
  esac

  printf '\n[%s] Starting %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${test_name}"
  echo "test_id=${test_id}" >> "${MANIFEST}"

  if ./scripts/run-k6-with-artifacts.sh \
    "${test_id}" \
    "${PROFILE}" \
    "${test_script}"; then
    :
  else
    exit_code=$?
    echo "failed_test=${test_id}" >> "${MANIFEST}"
    echo "finished_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "${MANIFEST}"
    printf 'Baseline suite stopped after %s.\n' "${test_name}" >&2
    exit "${exit_code}"
  fi

  # 직전 시험의 연결과 자원 상태가 정리될 짧은 간격을 둔다.
  sleep 5
done

if [[ "${start_found}" == false ]]; then
  echo "START_FROM does not match a baseline test: ${START_FROM}" >&2
  exit 1
fi

echo "finished_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "${MANIFEST}"
printf '\nBaseline suite completed: %s\n' "${SUITE_ID}"
