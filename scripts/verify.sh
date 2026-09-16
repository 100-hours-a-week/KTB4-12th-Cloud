#!/usr/bin/env bash

set -euo pipefail

ENV_FILE="${ENV_FILE:-.env}"
MODE="${1:-local}"

COMPOSE_ARGS=(
  --env-file "${ENV_FILE}"
  -f compose.yaml
)

if [[ "${MODE}" == "production" ]]; then
  COMPOSE_ARGS+=(-f compose.production.yaml)
fi

echo "[1/4] Validating Docker Compose configuration"
docker compose "${COMPOSE_ARGS[@]}" config --quiet

echo "[2/4] Checking container status"
docker compose "${COMPOSE_ARGS[@]}" ps

FRONTEND_PORT="$(
  docker compose "${COMPOSE_ARGS[@]}" port frontend 80 \
    | head -n 1 \
    | awk -F: '{print $NF}'
)"

if [[ -z "${FRONTEND_PORT}" ]]; then
  echo "Failed to determine the frontend port." >&2
  exit 1
fi

BASE_URL="http://localhost:${FRONTEND_PORT}"

echo "[3/4] Checking frontend"
curl --fail --silent --show-error \
  "${BASE_URL}/" \
  >/dev/null

echo "[4/4] Checking backend routing"
curl --fail --silent --show-error \
  "${BASE_URL}/api/health"

echo
echo "Docker Compose verification succeeded."