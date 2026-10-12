#!/usr/bin/env bash

set -euo pipefail

ENV_FILE="${1:-.env.develop}"

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Develop environment file does not exist: ${ENV_FILE}" >&2
  exit 1
fi

get_value() {
  local requested_key="$1"

  awk -F= -v requested_key="${requested_key}" '
    $1 == requested_key {
      sub(/^[^=]*=/, "")
      print
      exit
    }
  ' "${ENV_FILE}"
}

required=(
  COMPOSE_PROJECT_NAME
  HTTP_BIND_ADDRESS
  FE_IMAGE
  FE_VERSION
  BE_IMAGE
  BE_VERSION
  DB_VOLUME_NAME
  DB_NAME
  DB_USER
  DB_PASSWORD
  DB_ROOT_PASSWORD
  AWS_REGION
  PRODUCT_IMAGE_BUCKET
  JWT_SECRET_BASE64
  JWT_ISSUER
  LOGIN_RATE_LIMIT_HMAC_SECRET_BASE64
  CORS_ALLOWED_ORIGINS
  AI_PROFILE_BASE_URL
  PROFILING_SERVICE_TOKEN
)

for key in "${required[@]}"; do
  if [[ -z "$(get_value "${key}")" ]]; then
    echo "Missing required develop value: ${key}" >&2
    exit 1
  fi
done

if [[ "$(get_value HTTP_BIND_ADDRESS)" != "127.0.0.1" ]]; then
  echo "HTTP_BIND_ADDRESS must be 127.0.0.1 for Host Nginx proxying." >&2
  exit 1
fi

for key in DB_PASSWORD DB_ROOT_PASSWORD JWT_SECRET_BASE64 \
  LOGIN_RATE_LIMIT_HMAC_SECRET_BASE64 PROFILING_SERVICE_TOKEN; do
  if [[ "$(get_value "${key}")" == "change-me" ]]; then
    echo "Replace the placeholder develop secret: ${key}" >&2
    exit 1
  fi
done

if [[ "$(get_value PRODUCT_IMAGE_BUCKET)" == change-me* ]]; then
  echo "Replace the placeholder develop bucket name." >&2
  exit 1
fi

IMAGE_TAG_PATTERN='^([0-9]+\.[0-9]+\.[0-9]+|develop-[a-f0-9]{7,40})$'

for key in FE_VERSION BE_VERSION; do
  value="$(get_value "${key}")"
  if [[ ! "${value}" =~ ${IMAGE_TAG_PATTERN} || "${value}" == "develop-0000000" ]]; then
    echo "${key} must be a semantic version or an immutable develop-<git-sha> tag." >&2
    exit 1
  fi
done

if [[ "$(get_value COMPOSE_PROJECT_NAME)" != *develop* ]]; then
  echo "COMPOSE_PROJECT_NAME must identify the develop environment." >&2
  exit 1
fi

if [[ "$(get_value DB_VOLUME_NAME)" != *develop* ]]; then
  echo "DB_VOLUME_NAME must identify a develop-only volume." >&2
  exit 1
fi

if [[ "$(get_value DB_NAME)" != *develop* || "$(get_value DB_USER)" != *develop* ]]; then
  echo "DB_NAME and DB_USER must be develop-specific." >&2
  exit 1
fi

if [[ "$(get_value JWT_ISSUER)" != *develop* ]]; then
  echo "JWT_ISSUER must identify the develop environment." >&2
  exit 1
fi

if [[ "$(get_value CORS_ALLOWED_ORIGINS)" != "https://dev.seonjalal.com" ]]; then
  echo "CORS_ALLOWED_ORIGINS must be https://dev.seonjalal.com." >&2
  exit 1
fi

if [[ "$(get_value AWS_REGION)" != "ap-northeast-2" ]]; then
  echo "AWS_REGION must be ap-northeast-2." >&2
  exit 1
fi

echo "Develop environment contract is valid. Secret values were not printed."
