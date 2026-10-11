#!/usr/bin/env bash

set -Eeuo pipefail

deploy_path="${1:-/opt/seonjalal-develop}"
frontend_parameter="/seonjalal/develop/release/frontend_sha"
backend_parameter="/seonjalal/develop/release/backend_sha"

cd "${deploy_path}"

if [[ ! -f .env || ! -x scripts/deploy.sh ]]; then
  echo "Develop 배포 파일 또는 deploy.sh가 없음" >&2
  exit 1
fi

region="$(awk -F= '$1 == "AWS_REGION" {print $2; exit}' .env)"
region="${region:-ap-northeast-2}"

if [[ "${region}" != "ap-northeast-2" ]]; then
  echo "Develop 배포 Region은 ap-northeast-2여야 함" >&2
  exit 1
fi

# FE·BE Repository의 동시 Merge도 Host에서는 한 배포씩 처리한다.
# ec2-user가 소유한 배포 경로를 사용해 Root 권한 없이 Lock 파일을 생성한다.
lock_file="${deploy_path}/.deploy.lock"
exec 9>"${lock_file}"
if ! flock --wait 600 9; then
  echo "Develop 배포 Lock을 600초 안에 획득하지 못함" >&2
  exit 40
fi

frontend_sha="$(
  aws ssm get-parameter \
    --region "${region}" \
    --name "${frontend_parameter}" \
    --query 'Parameter.Value' \
    --output text
)"

backend_sha="$(
  aws ssm get-parameter \
    --region "${region}" \
    --name "${backend_parameter}" \
    --query 'Parameter.Value' \
    --output text
)"

for sha_name in frontend_sha backend_sha; do
  sha_value="${!sha_name}"
  if [[ ! "${sha_value}" =~ ^[a-f0-9]{40}$ ]]; then
    echo "${sha_name} Parameter가 40자리 소문자 Git SHA가 아님" >&2
    exit 1
  fi
done

current_frontend_image="$(
  awk -F= '$1 == "FRONTEND_IMAGE" {sub(/^[^=]*=/, ""); print; exit}' .env
)"
registry="${current_frontend_image%%/*}"

if [[ ! "${registry}" =~ ^[0-9]{12}\.dkr\.ecr\.ap-northeast-2\.amazonaws\.com$ ]]; then
  echo "현재 Frontend Image에서 허용된 ECR Registry를 확인하지 못함" >&2
  exit 1
fi

frontend_image="${registry}/seonjalal/develop/frontend:develop-${frontend_sha}"
main_backend_image="${registry}/seonjalal/develop/backend:develop-main-backend-${backend_sha}"
payment_server_image="${registry}/seonjalal/develop/backend:develop-payment-server-${backend_sha}"

backup_file="$(mktemp "${deploy_path}/.env.before-reconcile.XXXXXX")"
candidate_file="${deploy_path}/.env.candidate"

cleanup() {
  rm -f "${candidate_file}" "${backup_file}"
}
trap cleanup EXIT INT TERM

cp -a .env "${backup_file}"

# 현재 .env의 비민감 설정은 유지하고 세 Image URI만 원자적으로 교체한다.
python3 - \
  .env \
  "${candidate_file}" \
  "${frontend_image}" \
  "${main_backend_image}" \
  "${payment_server_image}" <<'PY'
import pathlib
import sys

source_path = pathlib.Path(sys.argv[1])
target_path = pathlib.Path(sys.argv[2])
replacement = {
    "FRONTEND_IMAGE": sys.argv[3],
    "MAIN_BACKEND_IMAGE": sys.argv[4],
    "PAYMENT_SERVER_IMAGE": sys.argv[5],
}

output = []
found = set()
for line in source_path.read_text().splitlines():
    key = line.split("=", 1)[0]
    if key in replacement:
        output.append(f"{key}={replacement[key]}")
        found.add(key)
    else:
        output.append(line)

missing = set(replacement) - found
if missing:
    raise SystemExit("missing image keys: " + ", ".join(sorted(missing)))

target_path.write_text("\n".join(output) + "\n")
PY

chmod 0600 "${candidate_file}"
chown --reference=.env "${candidate_file}"
mv "${candidate_file}" .env

set +e
"${deploy_path}/scripts/deploy.sh" "${deploy_path}"
deploy_status=$?
set -e

if [[ "${deploy_status}" -ne 0 ]]; then
  echo "ROLLBACK: 이전 Develop Candidate 복구 시작" >&2
  cp -a "${backup_file}" .env

  set +e
  "${deploy_path}/scripts/deploy.sh" "${deploy_path}"
  rollback_status=$?
  set -e

  if [[ "${rollback_status}" -ne 0 ]]; then
    echo "STOP: 신규 배포와 이전 Candidate 복구 모두 실패" >&2
    exit 30
  fi

  echo "PASS: 이전 Develop Candidate 복구 완료" >&2
  exit "${deploy_status}"
fi

install -m 0600 "${backup_file}" "${deploy_path}/.env.previous"
echo "PASS: Desired Develop Candidate 배포와 검증 완료"
