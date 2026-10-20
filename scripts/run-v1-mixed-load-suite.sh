#!/usr/bin/env bash

set -euo pipefail

# V1 핵심 9개 Endpoint의 가설 비율을 Baseline, Capacity, Spike, Stress 순서로 검증한다.
# 고정 Gift·Friend Fixture가 Test1 소유이므로 이번 V1 혼합 시험은 Test1 Seed 하나만 사용한다.

required_variables=(
  BASE_URL
  ACCESS_TOKENS_FILE
  JWT_SECRET_BASE64
  JWT_ISSUER
  PRODUCT_ID
  RECEIVED_GIFT_ID
  RECIPIENT_USER_ID
  SEARCH_QUERY
)

for variable_name in "${required_variables[@]}"; do
  if [[ -z "${!variable_name:-}" ]]; then
    echo "${variable_name} is required." >&2
    exit 1
  fi
done

if [[ "${BASE_URL%/}" != "https://v1-benchmark.seonjalal.com" ]]; then
  echo "The V1 mixed suite is allowed only on https://v1-benchmark.seonjalal.com." >&2
  exit 1
fi

if [[ ! -f "${ACCESS_TOKENS_FILE}" ]]; then
  echo "ACCESS_TOKENS_FILE does not exist." >&2
  exit 1
fi

umask 077
SUITE_ID="${SUITE_ID:-$(date -u +%Y%m%dT%H%M%SZ)-v1-mixed-load}"
SUITE_DIR="load-tests/results/${SUITE_ID}"
MANIFEST="${SUITE_DIR}/suite-manifest.txt"
FULL_TOKEN_FILE="${ACCESS_TOKENS_FILE}"
SINGLE_USER_TOKEN_FILE="$(mktemp "${PWD}/load-tests/secrets/mixed-single-user.XXXXXX")"

cleanup() {
  rm -f "${SINGLE_USER_TOKEN_FILE}"
  export ACCESS_TOKENS_FILE="${FULL_TOKEN_FILE}"
}

trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

jq '[.[0]]' "${FULL_TOKEN_FILE}" > "${SINGLE_USER_TOKEN_FILE}"
jq -e 'length == 1 and (.[0] | type == "string" and length > 0)' \
  "${SINGLE_USER_TOKEN_FILE}" >/dev/null
chmod 600 "${SINGLE_USER_TOKEN_FILE}"
export ACCESS_TOKENS_FILE="${SINGLE_USER_TOKEN_FILE}"

install -d -m 700 "${SUITE_DIR}"
{
  echo "suite_id=${SUITE_ID}"
  echo "started_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "auth_mode=direct_jwt_from_seed_subjects"
  echo "fixture_scope=single_user_test1"
  echo "profiles=baseline,capacity,spike,stress"
} > "${MANIFEST}"

profiles=(baseline capacity spike stress)

for profile in "${profiles[@]}"; do
  test_id="$(date -u +%Y%m%dT%H%M%SZ)-mixed-read-${profile}"
  printf '\n[%s] Starting mixed %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "${profile}"
  echo "test_id=${test_id}" >> "${MANIFEST}"

  set +e
  ./scripts/run-k6-with-artifacts.sh \
    "${test_id}" \
    "${profile}" \
    load-tests/k6/workloads/mixed-read.js
  exit_code=$?
  set -e

  echo "${profile}_k6_exit=${exit_code}" >> "${MANIFEST}"

  if [[ "${exit_code}" -eq 0 ]]; then
    sleep 10
    continue
  fi

  # Stress는 한계를 찾는 시험이므로 Threshold 초과에 의한 k6 99는 예상 결과로 보존한다.
  if [[ "${profile}" == "stress" && "${exit_code}" -eq 99 ]]; then
    echo "stress_result=threshold_crossed_as_expected" >> "${MANIFEST}"
    break
  fi

  echo "failed_profile=${profile}" >> "${MANIFEST}"
  echo "finished_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "${MANIFEST}"
  printf 'Mixed suite stopped after %s with exit %s.\n' \
    "${profile}" "${exit_code}" >&2
  exit "${exit_code}"
done

echo "finished_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "${MANIFEST}"
printf '\nMixed load suite completed: %s\n' "${SUITE_ID}"
