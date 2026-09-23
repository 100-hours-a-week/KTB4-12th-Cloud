#!/usr/bin/env bash

set -Eeuo pipefail

if [[ "$#" -ne 5 ]]; then
  echo "Usage: $0 DEPLOY_PATH FRONTEND_VERSION BACKEND_VERSION PUBLIC_BASE_URL READ_ONLY_SMOKE_PATH" >&2
  exit 2
fi

DEPLOY_PATH="$1"
FRONTEND_VERSION="$2"
BACKEND_VERSION="$3"
PUBLIC_BASE_URL="$4"
READ_ONLY_SMOKE_PATH="$5"

VERSION_PATTERN='^[0-9]+\.[0-9]+\.[0-9]+$'

if [[ ! "${DEPLOY_PATH}" =~ ^/[A-Za-z0-9._/-]+$ || "${DEPLOY_PATH}" == *".."* ]]; then
  echo "DEPLOY_PATH must be an absolute path without traversal segments." >&2
  exit 2
fi

if [[ ! "${FRONTEND_VERSION}" =~ ${VERSION_PATTERN} ]]; then
  echo "Invalid frontend version: ${FRONTEND_VERSION}" >&2
  exit 2
fi

if [[ ! "${BACKEND_VERSION}" =~ ${VERSION_PATTERN} ]]; then
  echo "Invalid backend version: ${BACKEND_VERSION}" >&2
  exit 2
fi

if [[ ! "${PUBLIC_BASE_URL}" =~ ^https://[A-Za-z0-9.-]+(:[0-9]+)?(/.*)?$ ]]; then
  echo "PUBLIC_BASE_URL must be a valid HTTPS URL." >&2
  exit 2
fi

if [[ ! "${READ_ONLY_SMOKE_PATH}" =~ ^/ || "${READ_ONLY_SMOKE_PATH}" =~ [[:space:]] ]]; then
  echo "READ_ONLY_SMOKE_PATH must start with / and contain no whitespace." >&2
  exit 2
fi

if [[ ! -d "${DEPLOY_PATH}" ]]; then
  echo "Deployment directory does not exist: ${DEPLOY_PATH}" >&2
  exit 1
fi

cd "${DEPLOY_PATH}"
umask 077

for required_file in .env compose.yaml compose.production.yaml scripts/verify.sh; do
  if [[ ! -f "${required_file}" ]]; then
    echo "Required deployment file is missing: ${DEPLOY_PATH}/${required_file}" >&2
    exit 1
  fi
done

if [[ ! -x scripts/verify.sh ]]; then
  echo "Verification script is not executable: ${DEPLOY_PATH}/scripts/verify.sh" >&2
  exit 1
fi

BASE_ENV=".env"
CANDIDATE_ENV="release.candidate.env"
CURRENT_ENV="release.current.env"
PREVIOUS_ENV="release.previous.env"
COMPOSE_FILES=(-f compose.yaml -f compose.production.yaml)

compose() {
  local env_file="$1"
  shift
  docker compose --env-file "${env_file}" "${COMPOSE_FILES[@]}" "$@"
}

wait_for_healthy() {
  local env_file="$1"
  local service="$2"
  local container_id
  local status

  container_id="$(compose "${env_file}" ps -q "${service}")"
  if [[ -z "${container_id}" ]]; then
    echo "Container for ${service} was not created." >&2
    return 1
  fi

  for attempt in {1..30}; do
    status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "${container_id}")"

    if [[ "${status}" == "healthy" ]]; then
      return 0
    fi

    if [[ "${status}" == "unhealthy" || "${status}" == "exited" || "${status}" == "dead" ]]; then
      echo "${service} entered ${status} state." >&2
      return 1
    fi

    echo "Waiting for ${service} health (${attempt}/30): ${status}"
    sleep 2
  done

  echo "Timed out waiting for ${service} health." >&2
  return 1
}

verify_external_routes() {
  local health_response

  curl --fail-with-body --silent --show-error \
    --retry 5 \
    --retry-all-errors \
    --retry-delay 2 \
    "${PUBLIC_BASE_URL}/" \
    >/dev/null

  health_response="$(
    curl --fail-with-body --silent --show-error \
      --retry 5 \
      --retry-all-errors \
      --retry-delay 2 \
      "${PUBLIC_BASE_URL}/api/health"
  )"
  grep --quiet --extended-regexp \
    '"status"[[:space:]]*:[[:space:]]*"UP"' \
    <<< "${health_response}"

  curl --fail-with-body --silent --show-error \
    --retry 5 \
    --retry-all-errors \
    --retry-delay 2 \
    "${PUBLIC_BASE_URL}${READ_ONLY_SMOKE_PATH}" \
    >/dev/null
}

rollback_on_error() {
  local deploy_exit="$?"
  trap - ERR
  set +e

  echo "Deployment failed. Collecting container status and logs." >&2
  compose "${CANDIDATE_ENV}" ps >&2
  compose "${CANDIDATE_ENV}" logs --no-color --tail=200 frontend backend >&2

  if [[ ! -s "${PREVIOUS_ENV}" ]]; then
    echo "No previous release is available. Manual recovery is required." >&2
    exit "${deploy_exit}"
  fi

  echo "Rolling back to the previous frontend/backend release." >&2
  compose "${PREVIOUS_ENV}" pull frontend backend
  compose "${PREVIOUS_ENV}" up -d --no-deps backend

  if ! wait_for_healthy "${PREVIOUS_ENV}" backend; then
    echo "Backend rollback verification failed. Stop automatic retries and recover manually." >&2
    exit "${deploy_exit}"
  fi

  compose "${PREVIOUS_ENV}" up -d --no-deps frontend

  if ! wait_for_healthy "${PREVIOUS_ENV}" frontend; then
    echo "Frontend rollback verification failed. Stop automatic retries and recover manually." >&2
    exit "${deploy_exit}"
  fi

  if ! ENV_FILE="${PREVIOUS_ENV}" ./scripts/verify.sh production; then
    echo "Rollback smoke test failed. Stop automatic retries and recover manually." >&2
    exit "${deploy_exit}"
  fi

  if ! verify_external_routes; then
    echo "Rollback external route verification failed. Stop automatic retries and recover manually." >&2
    exit "${deploy_exit}"
  fi

  cp "${PREVIOUS_ENV}" "${CURRENT_ENV}"
  chmod 600 "${CURRENT_ENV}"
  echo "Rollback succeeded. The deployment remains failed for investigation." >&2
  exit "${deploy_exit}"
}

cp "${BASE_ENV}" "${CANDIDATE_ENV}"
sed -i.bak -E '/^(FE_VERSION|BE_VERSION)=/d' "${CANDIDATE_ENV}"
rm -f "${CANDIDATE_ENV}.bak"
{
  echo "FE_VERSION=${FRONTEND_VERSION}"
  echo "BE_VERSION=${BACKEND_VERSION}"
} >> "${CANDIDATE_ENV}"
chmod 600 "${CANDIDATE_ENV}"

if [[ -s "${CURRENT_ENV}" ]]; then
  cp "${CURRENT_ENV}" "${PREVIOUS_ENV}"
else
  cp "${BASE_ENV}" "${PREVIOUS_ENV}"
fi
chmod 600 "${PREVIOUS_ENV}"

compose "${CANDIDATE_ENV}" config --quiet
compose "${CANDIDATE_ENV}" pull frontend backend

echo "Disk usage before deployment:"
df -h "${DEPLOY_PATH}"
echo "Memory usage before deployment:"
free -h

DATABASE_ID="$(compose "${CANDIDATE_ENV}" ps -q database)"
if [[ -z "${DATABASE_ID}" ]]; then
  echo "The production database container is not running." >&2
  exit 1
fi

DATABASE_STATUS="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "${DATABASE_ID}")"
if [[ "${DATABASE_STATUS}" != "healthy" ]]; then
  echo "The production database is not healthy: ${DATABASE_STATUS}" >&2
  exit 1
fi

# Preflight failures above leave the running release unchanged.
# Rollback starts only after the first application container is replaced.
trap rollback_on_error ERR

compose "${CANDIDATE_ENV}" up -d --no-deps backend
wait_for_healthy "${CANDIDATE_ENV}" backend

compose "${CANDIDATE_ENV}" up -d --no-deps frontend
wait_for_healthy "${CANDIDATE_ENV}" frontend

ENV_FILE="${CANDIDATE_ENV}" ./scripts/verify.sh production
verify_external_routes

cp "${CANDIDATE_ENV}" "${CURRENT_ENV}"
chmod 600 "${CURRENT_ENV}"

trap - ERR
echo "Production deployment succeeded."
