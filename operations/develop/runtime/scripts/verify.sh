#!/usr/bin/env bash

set -euo pipefail

deploy_path="${1:-/opt/seonjalal-develop}"
cd "${deploy_path}"

if [[ -z "${SECRET_ENV_FILE:-}" || ! -f "${SECRET_ENV_FILE}" ]]; then
  echo "배포 시 생성된 임시 Secret 환경 파일이 없음" >&2
  exit 1
fi

compose_args=(--env-file .env --env-file "${SECRET_ENV_FILE}")

echo "[1/4] Docker Compose 설정 검증"
sudo docker compose "${compose_args[@]}" config --quiet

echo "[2/4] Container Health 검증"
for service in database main-backend payment-server frontend; do
  container_id="$(sudo docker compose "${compose_args[@]}" ps -q "${service}")"
  if [[ -z "${container_id}" ]]; then
    echo "Container가 생성되지 않음: ${service}" >&2
    exit 1
  fi

  health="$(sudo docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "${container_id}")"
  if [[ "${health}" != "healthy" ]]; then
    echo "정상 상태가 아닌 Container: ${service} (${health})" >&2
    exit 1
  fi
done

echo "[3/4] Frontend Loopback 응답 검증"
curl --fail --silent --show-error http://127.0.0.1:8080/ >/dev/null

echo "[4/4] Backend Proxy Health 검증"
health_response="$(curl --fail --silent --show-error http://127.0.0.1:8080/api/actuator/health)"
grep --quiet --extended-regexp '"status"[[:space:]]*:[[:space:]]*"UP"' <<< "${health_response}"

echo "Develop Runtime 검증 완료"
