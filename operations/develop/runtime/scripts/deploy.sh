#!/usr/bin/env bash

set -Eeuo pipefail

deploy_path="${1:-/opt/seonjalal-develop}"
cd "${deploy_path}"

if [[ ! -f .env ]]; then
  echo "배포 환경 파일이 없음: ${deploy_path}/.env" >&2
  exit 1
fi

chmod 600 .env

for required_file in compose.yaml nginx/default.conf scripts/verify.sh; do
  if [[ ! -f "${required_file}" ]]; then
    echo "필수 배포 파일이 없음: ${required_file}" >&2
    exit 1
  fi
done

required_keys=(
  FRONTEND_IMAGE
  MAIN_BACKEND_IMAGE
  PAYMENT_SERVER_IMAGE
  DB_NAME
  DB_USER
  PRODUCT_IMAGE_BUCKET
  JWT_ISSUER
  CORS_ALLOWED_ORIGINS
  RUNTIME_SECRET_ID
  PAYMENT_SERVER_BASE_URL
)

# 기존 활성 환경의 계약을 유지하되 명시적으로 끈 환경에는 AI Secret을 요구하지 않는다.
ai_chat_enabled="$(awk -F= '$1 == "AI_CHAT_ENABLED" {print $2; exit}' .env)"
ai_chat_enabled="${ai_chat_enabled:-true}"
if [[ "${ai_chat_enabled}" != "true" && "${ai_chat_enabled}" != "false" ]]; then
  echo "AI_CHAT_ENABLED는 true 또는 false여야 함" >&2
  exit 1
fi

# Compose도 false로 고정하지만, 명시적인 ES 활성화 오입력은 조용히 덮지 않고 거절.
product_search_enabled="$(awk -F= '$1 == "PRODUCT_SEARCH_ENABLED" {print $2; exit}' .env)"
product_search_enabled="${product_search_enabled:-false}"
if [[ "${product_search_enabled}" != "false" ]]; then
  echo "PRODUCT_SEARCH_ENABLED는 현재 배포에서 false만 허용" >&2
  exit 1
fi
if [[ "${ai_chat_enabled}" == "true" ]]; then
  required_keys+=(
    AI_CHAT_BASE_URL
    AI_CHAT_CONNECT_TIMEOUT
    AI_CHAT_READ_TIMEOUT
    AI_CHAT_SSE_IDLE_TIMEOUT
    AI_CHAT_SSE_TOTAL_TIMEOUT
    AI_CHAT_SSE_MAX_CONNECTIONS
    CHAT_CONVERSATION_TOKEN_ISSUER
  )
fi

for key in "${required_keys[@]}"; do
  if ! grep --quiet --extended-regexp "^${key}=.+" .env; then
    echo "필수 환경변수가 없거나 비어 있음: ${key}" >&2
    exit 1
  fi
done

if grep --quiet --extended-regexp '=(change-me|.*000000000000.*|.*YOUR_TAILNET.*)$' .env; then
  echo "실제 값으로 교체되지 않은 Placeholder 존재" >&2
  exit 1
fi

image_pattern='^[0-9]{12}\.dkr\.ecr\.ap-northeast-2\.amazonaws\.com/seonjalal/develop/'
for key in FRONTEND_IMAGE MAIN_BACKEND_IMAGE PAYMENT_SERVER_IMAGE; do
  image="$(awk -F= -v key="${key}" '$1 == key {sub(/^[^=]*=/, ""); print; exit}' .env)"
  if [[ ! "${image}" =~ ${image_pattern} ]]; then
    echo "허용되지 않은 ECR Image 경로: ${key}" >&2
    exit 1
  fi
done

registry="$(awk -F= '$1 == "FRONTEND_IMAGE" {sub(/^[^=]*=/, ""); split($0, parts, "/"); print parts[1]; exit}' .env)"
region="$(awk -F= '$1 == "AWS_REGION" {print $2; exit}' .env)"
region="${region:-ap-northeast-2}"
secret_id="$(awk -F= '$1 == "RUNTIME_SECRET_ID" {sub(/^[^=]*=/, ""); print; exit}' .env)"

# Secret을 Disk에 상시 보관하지 않고 배포 Process가 살아 있는 동안에만 /run에 생성한다.
secret_directory="/run/seonjalal-develop"
runtime_user="$(id -un)"
runtime_group="$(id -gn)"
sudo install -d -o "${runtime_user}" -g "${runtime_group}" -m 0700 "${secret_directory}"
secret_env_file="$(mktemp "${secret_directory}/runtime-secrets.XXXXXX.env")"
chmod 600 "${secret_env_file}"

cleanup() {
  rm -f "${secret_env_file}"

  # ECR 인증 Token은 Image Pull 이후 필요하지 않으므로 Host Disk에 남기지 않는다.
  if [[ -n "${registry:-}" ]]; then
    sudo docker logout "${registry}" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT INT TERM

# JSON Key 누락과 dotenv 해석에 위험한 문자를 차단하며 Secret 값 자체는 출력하지 않는다.
AWS_MAX_ATTEMPTS=2 aws secretsmanager get-secret-value \
  --region "${region}" \
  --cli-connect-timeout 5 \
  --cli-read-timeout 15 \
  --secret-id "${secret_id}" \
  --query SecretString \
  --output text |
  AI_CHAT_ENABLED="${ai_chat_enabled}" python3 -c '
import json
import os
import re
import sys

required_keys = (
    "DB_PASSWORD",
    "DB_ROOT_PASSWORD",
    "JWT_SECRET_BASE64",
    "LOGIN_RATE_LIMIT_HMAC_SECRET_BASE64",
    "PROFILING_SERVICE_TOKEN",
    "PAYMENT_SERVER_API_KEY",
    "REDIS_PASSWORD",
    "SMTP_USERNAME",
    "SMTP_PASSWORD",
)
if os.environ["AI_CHAT_ENABLED"] == "true":
    required_keys += ("AI_CHAT_SERVICE_TOKEN", "CHAT_CONVERSATION_TOKEN_SECRET_BASE64")

optional_keys = ("DISCORD_WEBHOOK_URL", "GOOGLE_SERVICE_ACCOUNT_JSON_BASE64")

raw = sys.stdin.read()
if not raw.strip():
    raise SystemExit("Runtime Secret 조회 결과가 없어 배포 중단")
try:
    payload = json.loads(raw)
except json.JSONDecodeError:
    raise SystemExit("Runtime Secret JSON 형식 오류") from None
if not isinstance(payload, dict):
    raise SystemExit("Runtime Secret은 JSON 객체여야 함")
missing = [key for key in required_keys if not isinstance(payload.get(key), str) or not payload[key]]
if missing:
    raise SystemExit("필수 Secret Key가 없거나 비어 있음: " + ", ".join(missing))

selected_keys = required_keys + tuple(key for key in optional_keys if payload.get(key))
safe_value = re.compile(r"^[A-Za-z0-9_./+=:@%-]+$")
invalid = [key for key in selected_keys if not isinstance(payload[key], str) or not safe_value.fullmatch(payload[key])]
if invalid:
    raise SystemExit("dotenv에 안전하지 않은 문자가 포함된 Secret Key: " + ", ".join(invalid))

for key in selected_keys:
    print(f"{key}={payload[key]}")
' > "${secret_env_file}"

compose_args=(--env-file .env --env-file "${secret_env_file}")

# EC2 Instance Role의 단기 자격 증명으로만 ECR에 로그인한다.
aws ecr get-login-password --region "${region}" |
  sudo docker login --username AWS --password-stdin "${registry}"

sudo docker compose "${compose_args[@]}" config --quiet
# 정상 DB가 있으면 이미지 pull·컨테이너 재생성 없이 앱만 갱신한다.
# 최초 구축에서만 DB를 생성하며, 기존 DB가 비정상이면 자동 교체하지 않는다.
database_id="$(sudo docker compose "${compose_args[@]}" ps --all --quiet database)"
if [[ -n "${database_id}" ]]; then
  database_health="$(sudo docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "${database_id}")"
  if [[ "${database_health}" != "healthy" ]]; then
    echo "기존 Develop DB가 healthy가 아니므로 자동 교체 없이 배포 중단" >&2
    exit 1
  fi
else
  sudo docker compose "${compose_args[@]}" pull database
  sudo docker compose "${compose_args[@]}" up --detach --no-deps --wait --wait-timeout 240 database
fi

# 앱은 --no-deps로 하나씩 기동하므로 Backend가 의존하는 Redis를 먼저 기동한다. 변경이 없으면 컨테이너를 재생성하지 않는다.
sudo docker compose "${compose_args[@]}" pull redis
sudo docker compose "${compose_args[@]}" up --detach --no-deps --wait --wait-timeout 120 redis

sudo docker compose "${compose_args[@]}" pull main-backend payment-server frontend
# Main의 Flyway 및 Health 완료 후 Payment, Frontend 순으로 기동.
for application_service in main-backend payment-server frontend; do
  if sudo docker compose "${compose_args[@]}" up --detach --no-deps --wait --wait-timeout 240 "${application_service}"; then
    continue
  else
    deploy_status=$?
    # 롤백이 실패한 컨테이너를 교체하기 전에 시작 오류를 남긴다. 비밀값은 환경변수로만 전달되어 로그에 출력되지 않는다.
    echo "배포 실패: ${application_service} 최근 로그(100줄)" >&2
    sudo docker compose "${compose_args[@]}" logs --no-color --tail 100 "${application_service}" >&2 || true
    exit "${deploy_status}"
  fi
done

SECRET_ENV_FILE="${secret_env_file}" "${deploy_path}/scripts/verify.sh" "${deploy_path}"
