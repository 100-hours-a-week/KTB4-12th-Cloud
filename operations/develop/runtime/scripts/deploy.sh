#!/usr/bin/env bash

# ==============================================================================
# Develop 환경 런타임 배포 스크립트 (Develop EC2에서 실행. 기본 경로 /opt/seonjalal-develop)
# ------------------------------------------------------------------------------
# 역할
#   - Develop EC2의 Docker Compose 스택(database, main-backend, payment-server, frontend)을
#     .env에 적힌 ECR 이미지 기준으로 갱신한다. Secret은 Secrets Manager에서 읽어 임시 파일로만 사용.
# 호출처(코드로 확인)
#   - operations/develop/runtime/scripts/reconcile.sh 가 이 스크립트를 호출한다.
#   - 이 저장소의 GitHub Actions 워크플로에는 이 스크립트를 직접 호출하는 것이 없다.
# 확인 필요: 일반 Develop 배포의 실제 트리거(수동 SSM/SSH 여부)와 .env의 *_IMAGE 값을 갱신하는 주체
# 입력 -> 출력
#   - 입력: 인자 1개(배포 경로, 선택), 배포 경로의 .env(공개 설정+이미지 URI), Secrets Manager의 Runtime Secret.
#   - 출력(부작용): 앱 컨테이너 재생성(최초 구축 시에만 DB 컨테이너 생성), 마지막에 verify.sh 실행.
# 흐름
#   1) .env/필수 파일 확인 -> 2) 필수 키·placeholder·ECR 경로 검증(외부 호출 전에 거절)
#   -> 3) Secret 조회/검증 후 /run 아래 임시 env 파일 생성 -> 4) ECR 로그인
#   -> 5) DB 상태 확인(없으면 최초 생성, 비정상이면 중단) -> 6) 이미지 pull
#   -> 7) main-backend -> payment-server -> frontend 순서로 기동(각각 healthy 대기)
#   -> 8) verify.sh 스모크 검증
# 실패 시 동작
#   - set -e로 즉시 중단. 자동 롤백은 없다. 중간에 실패하면 일부 컨테이너만 새 이미지일 수 있으므로
#     .env의 이미지 값을 이전 버전으로 되돌려 다시 실행해야 한다(수동 복구).
#   - 종료 시 trap이 Secret 임시 파일 삭제와 ECR docker logout을 수행(성공/실패/신호 모두).
#   - DB는 정상일 때 재생성하지 않으며 비정상이면 자동 교체 없이 중단한다.
# 주의(테스트 연동)
#   - scripts/prepare-develop-app-recovery.py와 scripts/test_develop_runtime_contract.py가 이 파일의
#     특정 문자열/구조를 텍스트로 찾는다. 코드를 바꿀 때는 해당 스크립트/테스트도 함께 확인할 것.
# ==============================================================================
set -Eeuo pipefail

deploy_path="${1:-/opt/seonjalal-develop}"
cd "${deploy_path}"

if [[ ! -f .env ]]; then
  echo "배포 환경 파일이 없음: ${deploy_path}/.env" >&2
  exit 1
fi

# .env에는 공개 설정만 있어야 하나(Secret은 Secrets Manager) 안전을 위해 소유자 전용 권한으로 고정.
chmod 600 .env

# 배포에 필요한 파일이 EC2에 배치돼 있는지 확인. 없으면 아무 변경 없이 중단.
for required_file in compose.yaml nginx/default.conf scripts/verify.sh; do
  if [[ ! -f "${required_file}" ]]; then
    echo "필수 배포 파일이 없음: ${required_file}" >&2
    exit 1
  fi
done

# .env에 반드시 있어야 하는 공개 설정 키 목록. 값이 비어 있으면 아래 검사에서 실패한다.
# Secret 값은 .env가 아니라 Secrets Manager에서 가져온다.
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
# AI 채팅을 켠 환경에서만 AI 연동 관련 키를 필수로 요구한다.
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

# 위 목록을 모두 확인. 이 단계까지는 AWS/Docker를 호출하지 않으므로 설정 오류로 인한 실패는 부작용이 없다.
for key in "${required_keys[@]}"; do
  if ! grep --quiet --extended-regexp "^${key}=.+" .env; then
    echo "필수 환경변수가 없거나 비어 있음: ${key}" >&2
    exit 1
  fi
done

# 템플릿(.env.example 등)에서 복사한 placeholder 값이 남아 있으면 배포하지 않는다.
if grep --quiet --extended-regexp '=(change-me|.*000000000000.*|.*YOUR_TAILNET.*)$' .env; then
  echo "실제 값으로 교체되지 않은 Placeholder 존재" >&2
  exit 1
fi

# 이미지는 Develop용 ECR 저장소 경로(seonjalal/develop/)만 허용. production 이미지나 외부 레지스트리
# 이미지가 Develop에 배포되는 실수를 차단한다.
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

# Secret 임시 파일과 ECR 로그인 토큰을 남기지 않기 위한 정리 함수. 아래 trap으로 정상 종료/실패/신호에 모두 실행.
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

# 이후 모든 compose 호출은 공개 설정(.env) + 임시 Secret 파일 두 개를 함께 사용한다.
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

sudo docker compose "${compose_args[@]}" pull main-backend payment-server frontend
# Main의 Flyway 및 Health 완료 후 Payment, Frontend 순으로 기동.
for application_service in main-backend payment-server frontend; do
  sudo docker compose "${compose_args[@]}" up --detach --no-deps --wait --wait-timeout 240 "${application_service}"
done

# 배포 후 검증(컨테이너 Health, 스모크). 임시 Secret 파일 경로를 환경변수로 넘기며 실패하면 이 스크립트도 실패.
SECRET_ENV_FILE="${secret_env_file}" "${deploy_path}/scripts/verify.sh" "${deploy_path}"
