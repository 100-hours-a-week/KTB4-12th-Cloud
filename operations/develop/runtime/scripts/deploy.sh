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
)

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
aws secretsmanager get-secret-value \
  --region "${region}" \
  --secret-id "${secret_id}" \
  --query SecretString \
  --output text |
  python3 -c '
import json
import re
import sys

required_keys = (
    "DB_PASSWORD",
    "DB_ROOT_PASSWORD",
    "JWT_SECRET_BASE64",
    "LOGIN_RATE_LIMIT_HMAC_SECRET_BASE64",
    "PROFILING_SERVICE_TOKEN",
)

payload = json.load(sys.stdin)
missing = [key for key in required_keys if not isinstance(payload.get(key), str) or not payload[key]]
if missing:
    raise SystemExit("필수 Secret Key가 없거나 비어 있음: " + ", ".join(missing))

safe_value = re.compile(r"^[A-Za-z0-9_./+=:@%-]+$")
invalid = [key for key in required_keys if not safe_value.fullmatch(payload[key])]
if invalid:
    raise SystemExit("dotenv에 안전하지 않은 문자가 포함된 Secret Key: " + ", ".join(invalid))

for key in required_keys:
    print(f"{key}={payload[key]}")
' > "${secret_env_file}"

compose_args=(--env-file .env --env-file "${secret_env_file}")

# EC2 Instance Role의 단기 자격 증명으로만 ECR에 로그인한다.
aws ecr get-login-password --region "${region}" |
  sudo docker login --username AWS --password-stdin "${registry}"

sudo docker compose "${compose_args[@]}" config --quiet
sudo docker compose "${compose_args[@]}" pull
sudo docker compose "${compose_args[@]}" up --detach --remove-orphans --wait --wait-timeout 240

SECRET_ENV_FILE="${secret_env_file}" "${deploy_path}/scripts/verify.sh" "${deploy_path}"
