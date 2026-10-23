#!/usr/bin/env bash

# ==============================================================================
# V1 운영 EC2 배포 스크립트 (운영 EC2에서 ec2-user로 실행됨)
# ------------------------------------------------------------------------------
# 역할
#   - 운영 EC2의 Docker Compose 스택에서 backend -> frontend 순서로 지정 버전으로 교체하고,
#     헬스체크와 외부 경로 스모크 테스트에 실패하면 직전 릴리스로 자동 롤백한다.
# 호출처
#   - scripts/deploy-production-via-ssm.sh가 SSM Run Command로 이 파일 내용을 EC2에 보내 실행.
#     (직접 실행도 가능하나 일반 경로는 cd-production.yml)
# 입력
#   - 인자 5개: DEPLOY_PATH FRONTEND_VERSION BACKEND_VERSION PUBLIC_BASE_URL READ_ONLY_SMOKE_PATH
#   - DEPLOY_PATH 안에 .env, compose.yaml, compose.production.yaml, scripts/verify.sh 필요.
# 출력(부작용)
#   - release.candidate.env(후보), release.previous.env(롤백 대상), release.current.env(마지막 성공 릴리스)
#     를 DEPLOY_PATH에 생성/갱신(모두 .env 복사본이라 Secret 포함 -> 권한 600, umask 077).
#   - backend/frontend 컨테이너 재생성. DB 컨테이너는 건드리지 않음(--no-deps).
# 흐름
#   1) 인자/경로/필수 파일 검증 -> 2) 후보 env 생성(FE_VERSION/BE_VERSION만 교체)
#   -> 3) 롤백 대상 env 확보 -> 4) compose 설정 검증, 이미지 pull
#   -> 5) 디스크/메모리 출력, DB healthy 확인(여기까지 실패해도 운영 릴리스는 그대로)
#   -> 6) ERR trap(롤백) 활성화 후 backend 교체 -> healthy 대기 -> frontend 교체 -> healthy 대기
#   -> 7) verify.sh 스모크 + 외부 URL 검증 -> 8) 성공 시 current env 갱신
# 실패 시 동작
#   - 6)~7) 중 실패: rollback_on_error가 로그 수집 후 직전 릴리스로 backend -> frontend 복구하고
#     검증한다. 롤백이 성공해도 종료 코드는 원래 실패 코드를 유지(배포는 실패로 기록).
#   - 롤백 대상이 없거나 롤백 검증이 실패하면 자동 재시도 없이 중단 -> 수동 복구 필요.
#   - DB 스키마/데이터는 롤백하지 않는다.
# ==============================================================================
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

# 입력 검증(워크플로에서 한 번 검증했어도 이 스크립트를 단독 실행할 수 있으므로 재검증).
# 경로 조작(..), 형식이 다른 버전, HTTP URL 등을 거부하며 종료 코드 2는 "인자 오류".
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

# 여기부터는 서버 상태 검사. 종료 코드 1은 "환경/상태 문제".
if [[ ! -d "${DEPLOY_PATH}" ]]; then
  echo "Deployment directory does not exist: ${DEPLOY_PATH}" >&2
  exit 1
fi

# DEPLOY_PATH로 이동. 이어지는 umask 077은 이후 생성되는 env 복사본(Secret 포함)이 다른 사용자에게 읽히지 않게 하기 위한 것.
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

# env 파일 역할: .env=고정 설정(Secret 포함, 수동 관리) / candidate=이번 배포 대상 /
# current=마지막으로 성공한 릴리스 / previous=롤백 시 복원할 릴리스.
# # 확인 필요: .env, compose.yaml, compose.production.yaml, scripts/verify.sh를 EC2에 배치/갱신하는 주체(이 CD는 버전 값만 바꿈)
BASE_ENV=".env"
CANDIDATE_ENV="release.candidate.env"
CURRENT_ENV="release.current.env"
PREVIOUS_ENV="release.previous.env"
COMPOSE_FILES=(-f compose.yaml -f compose.production.yaml)

# 항상 같은 compose 파일 조합 + 지정한 env 파일로 docker compose를 호출하는 래퍼.
compose() {
  local env_file="$1"
  shift
  docker compose --env-file "${env_file}" "${COMPOSE_FILES[@]}" "$@"
}

# 컨테이너 Health가 healthy가 될 때까지 최대 약 60초(2초*30회) 대기.
# unhealthy/exited/dead면 즉시 실패, 시간 초과도 실패. Healthcheck가 없는 컨테이너는 running 상태만 본다.
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

# EC2에서 공개 URL(PUBLIC_BASE_URL)로 직접 요청해 실제 외부 경로 기준으로 확인한다:
#  / (프론트 응답), /api/health (status=UP), 읽기 전용 스모크 경로. 각 요청은 5회까지 재시도.
# 쓰기 요청은 하지 않아 운영 데이터를 변경하지 않는다.
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

# [롤백] ERR trap 핸들러. 배포 중 어떤 명령이든 실패하면 호출된다.
# 순서: 실패 원인 로그 수집 -> previous env로 backend 복구/healthy 확인 -> frontend 복구/healthy 확인
# -> verify.sh와 외부 경로 재검증 -> 성공하면 current를 previous로 되돌림.
# trap - ERR / set +e: 롤백 중 오류가 재귀적으로 이 핸들러를 부르지 않게 하고, 단계별로 직접 판정.
# 어느 경우든 마지막은 원래 실패 종료 코드로 exit하여 워크플로를 실패로 남긴다.
rollback_on_error() {
  local deploy_exit="$?"
  trap - ERR
  set +e

  echo "Deployment failed. Collecting container status and logs." >&2
  compose "${CANDIDATE_ENV}" ps >&2
  compose "${CANDIDATE_ENV}" logs --no-color --tail=200 frontend backend >&2

  # 롤백 대상 env가 비어 있으면 되돌릴 곳이 없으므로 수동 복구로 넘긴다.
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

# [후보 env 생성] .env를 복사하고 FE_VERSION/BE_VERSION 줄만 지운 뒤 새 버전을 덧붙인다.
# 원본 .env는 수정하지 않는다. sed -i.bak은 GNU/BSD sed 모두에서 동작하게 하는 관용 형태.
cp "${BASE_ENV}" "${CANDIDATE_ENV}"
sed -i.bak -E '/^(FE_VERSION|BE_VERSION)=/d' "${CANDIDATE_ENV}"
rm -f "${CANDIDATE_ENV}.bak"
{
  echo "FE_VERSION=${FRONTEND_VERSION}"
  echo "BE_VERSION=${BACKEND_VERSION}"
} >> "${CANDIDATE_ENV}"
chmod 600 "${CANDIDATE_ENV}"

# [롤백 지점 확보] 마지막 성공 릴리스(current)를 previous로 복사. 첫 CD 실행처럼 current가 없으면
# .env를 대신 사용한다.
# # 확인 필요: current가 없는 첫 실행에서 .env의 FE_VERSION/BE_VERSION이 유효한 이전 버전인지(롤백 가능 여부)
if [[ -s "${CURRENT_ENV}" ]]; then
  cp "${CURRENT_ENV}" "${PREVIOUS_ENV}"
else
  cp "${BASE_ENV}" "${PREVIOUS_ENV}"
fi
chmod 600 "${PREVIOUS_ENV}"

# 설정 문법/변수 치환 검증과 새 이미지 사전 pull. 아직 실행 중인 컨테이너는 바뀌지 않는다
# (pull 실패 시 운영 릴리스 그대로 종료).
compose "${CANDIDATE_ENV}" config --quiet
compose "${CANDIDATE_ENV}" pull frontend backend

# 배포 전 자원 상태를 로그로 남겨 용량/메모리 부족 장애 분석에 쓴다(정보 출력만).
echo "Disk usage before deployment:"
df -h "${DEPLOY_PATH}"
echo "Memory usage before deployment:"
free -h

# DB가 정상(healthy)이 아닌 상태에서 앱을 교체하지 않는다. 이 검사 실패는 롤백 없이 종료
# (아직 아무것도 바꾸지 않았기 때문).
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
# 여기부터 컨테이너 교체가 시작되므로 롤백을 활성화한다. set -E 덕분에 함수 안의 실패도 잡힌다.
# 단, if 조건식 안에서의 실패는 ERR trap이 발동하지 않는다.
trap rollback_on_error ERR

# backend 먼저 교체(--no-deps: DB 등 의존 컨테이너는 재생성하지 않음). 이 시점부터 해당 컨테이너는
# 새 버전. healthy가 되어야 frontend로 진행.
compose "${CANDIDATE_ENV}" up -d --no-deps backend
wait_for_healthy "${CANDIDATE_ENV}" backend

compose "${CANDIDATE_ENV}" up -d --no-deps frontend
wait_for_healthy "${CANDIDATE_ENV}" frontend

# 애플리케이션 스모크 테스트(내용은 scripts/verify.sh) -> 외부 URL 기준 확인.
# 둘 다 통과해야 배포 성공으로 보고 current를 갱신한다.
ENV_FILE="${CANDIDATE_ENV}" ./scripts/verify.sh production
verify_external_routes

# 성공 확정: 후보를 마지막 성공 릴리스로 기록(다음 배포의 롤백 지점이 됨). 이후 trap 해제.
cp "${CANDIDATE_ENV}" "${CURRENT_ENV}"
chmod 600 "${CURRENT_ENV}"

trap - ERR
echo "Production deployment succeeded."
