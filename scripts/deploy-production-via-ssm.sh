#!/usr/bin/env bash

# ==============================================================================
# SSM 경유 운영 배포 래퍼 (GitHub Actions 러너에서 실행됨. EC2에서 실행되는 것이 아님)
# ------------------------------------------------------------------------------
# 역할
#   - 같은 저장소의 scripts/deploy-production.sh를 gzip+base64로 압축해 SSM Run Command
#     (AWS-RunShellScript)로 대상 EC2에 보내 ec2-user 권한으로 실행시키고, 끝날 때까지
#     폴링해서 결과를 러너 로그로 가져온 뒤 성공/실패를 종료 코드로 돌려준다.
# 호출처
#   - .github/workflows/cd-production.yml (Legacy V1 운영 배포)
# 입력
#   - 인자 6개: INSTANCE_ID DEPLOY_PATH FRONTEND_VERSION BACKEND_VERSION
#     PUBLIC_BASE_URL READ_ONLY_SMOKE_PATH
#   - 선택 환경변수: REMOTE_SCRIPT_PATH(기본 scripts/deploy-production.sh, 저장소 루트 기준),
#     SSM_EXECUTION_TIMEOUT_SECONDS(기본 900), SSM_POLL_INTERVAL_SECONDS(5), SSM_MAX_POLLS(190)
# 출력
#   - SSM 표준출력/표준에러를 Actions 로그 그룹으로 출력. 종료 코드 0=배포 성공, 그 외 실패.
# 흐름
#   1) 인자/도구/인스턴스 ID 형식 검증 -> 2) 스크립트 압축 -> 3) 원격 실행용 래퍼 생성
#   -> 4) ssm send-command -> 5) get-command-invocation 폴링 -> 6) 결과 출력과 상태 판정
# 실패 시 동작
#   - 이 스크립트는 롤백하지 않는다. 롤백은 원격 deploy-production.sh의 ERR trap이 수행.
#   - SSM 상태가 Success가 아니면(Failed/TimedOut/Cancelled 등) exit 1.
# 보안
#   - Secret은 전달하지 않는다. 버전/URL/경로 같은 비밀이 아닌 값만 인자로 보낸다
#     (SSM 명령 이력에 남을 수 있음). 운영 .env의 Secret은 EC2 안에만 존재.
# ==============================================================================
set -euo pipefail

if [[ "$#" -ne 6 ]]; then
  echo "Usage: $0 INSTANCE_ID DEPLOY_PATH FRONTEND_VERSION BACKEND_VERSION PUBLIC_BASE_URL READ_ONLY_SMOKE_PATH" >&2
  exit 2
fi

INSTANCE_ID="$1"
DEPLOY_PATH="$2"
FRONTEND_VERSION="$3"
BACKEND_VERSION="$4"
PUBLIC_BASE_URL="$5"
READ_ONLY_SMOKE_PATH="$6"
# 기본값은 저장소 루트 기준 상대경로이므로 루트에서 실행해야 한다(워크플로가 그렇게 호출).
REMOTE_SCRIPT_PATH="${REMOTE_SCRIPT_PATH:-scripts/deploy-production.sh}"
# SSM 명령 자체 실행 한도(900초). 폴링 한도(5초*190회=950초)는 이보다 약간 길게 잡아
# SSM이 TimedOut으로 끝나는 것을 래퍼가 관찰할 수 있게 한 것으로 보인다.
SSM_EXECUTION_TIMEOUT_SECONDS="${SSM_EXECUTION_TIMEOUT_SECONDS:-900}"
SSM_POLL_INTERVAL_SECONDS="${SSM_POLL_INTERVAL_SECONDS:-5}"
SSM_MAX_POLLS="${SSM_MAX_POLLS:-190}"

# 러너에 필요한 도구 사전 확인. 없으면 AWS 호출 전에 실패.
for required_command in aws base64 gzip jq; do
  if ! command -v "${required_command}" >/dev/null 2>&1; then
    echo "Required command is not installed: ${required_command}" >&2
    exit 1
  fi
done

if [[ ! -f "${REMOTE_SCRIPT_PATH}" ]]; then
  echo "Remote deployment script does not exist: ${REMOTE_SCRIPT_PATH}" >&2
  exit 1
fi

if [[ ! "${INSTANCE_ID}" =~ ^i-[a-f0-9]{8,17}$ ]]; then
  echo "Invalid EC2 instance ID: ${INSTANCE_ID}" >&2
  exit 2
fi

if [[ ! "${SSM_EXECUTION_TIMEOUT_SECONDS}" =~ ^[0-9]+$ || "${SSM_EXECUTION_TIMEOUT_SECONDS}" -lt 30 ]]; then
  echo "SSM_EXECUTION_TIMEOUT_SECONDS must be an integer of at least 30." >&2
  exit 2
fi

# 배포 스크립트를 파일 전송 없이 SSM 명령 문자열에 실어 보내기 위해 gzip+base64로 인코딩.
# (EC2에 미리 스크립트를 배치할 필요가 없고, 항상 이 커밋의 스크립트가 실행되는 효과)
SCRIPT_PAYLOAD="$(gzip -c "${REMOTE_SCRIPT_PATH}" | base64 | tr -d '\n')"

# 원격 쉘에서 단어 분리/인젝션이 일어나지 않도록 인자를 %q로 쉘 이스케이프.
printf -v DEPLOY_PATH_Q '%q' "${DEPLOY_PATH}"
printf -v FRONTEND_VERSION_Q '%q' "${FRONTEND_VERSION}"
printf -v BACKEND_VERSION_Q '%q' "${BACKEND_VERSION}"
printf -v PUBLIC_BASE_URL_Q '%q' "${PUBLIC_BASE_URL}"
printf -v READ_ONLY_SMOKE_PATH_Q '%q' "${READ_ONLY_SMOKE_PATH}"

# EC2에서 실행될 쉘 코드(heredoc). \$ 로 이스케이프한 부분은 EC2에서 평가되고,
# 이스케이프 안 한 ${...}는 지금 러너에서 값이 치환된다.
# 동작: 임시 파일에 스크립트 복원 -> ec2-user 소유로 바꾸고 -> ec2-user 권한으로 실행 -> 종료 시 삭제.
# SSM 에이전트는 root로 실행되므로 runuser로 권한을 낮춰 docker/배포 파일은 ec2-user 기준으로 다룬다.
REMOTE_WRAPPER="$(cat <<EOF
set -Eeuo pipefail
temporary_script="\$(mktemp /tmp/seonjalal-production-deploy.XXXXXX)"
cleanup() {
  rm -f "\${temporary_script}"
}
trap cleanup EXIT
printf '%s' '${SCRIPT_PAYLOAD}' | base64 --decode | gzip --decompress > "\${temporary_script}"
chmod 700 "\${temporary_script}"
chown ec2-user:ec2-user "\${temporary_script}"
runuser -u ec2-user -- "\${temporary_script}" ${DEPLOY_PATH_Q} ${FRONTEND_VERSION_Q} ${BACKEND_VERSION_Q} ${PUBLIC_BASE_URL_Q} ${READ_ONLY_SMOKE_PATH_Q}
EOF
)"

printf -v REMOTE_WRAPPER_Q '%q' "${REMOTE_WRAPPER}"
# 로그인 쉘(-l)로 실행해 ec2-user 환경 설정(PATH 등)을 로드하려는 의도로 보인다.
SSM_COMMAND="bash -lc ${REMOTE_WRAPPER_Q}"
SSM_PARAMETERS="$(
  jq -cn \
    --arg command "${SSM_COMMAND}" \
    --arg execution_timeout "${SSM_EXECUTION_TIMEOUT_SECONDS}" \
    '{commands: [$command], executionTimeout: [$execution_timeout]}'
)"

# [부작용] 여기서 실제로 운영 EC2에 배포 명령이 전달된다. --timeout-seconds 60은 SSM이 명령을
# 인스턴스에 전달·시작하기까지 기다리는 시간이고, 실행 시간 한도는 위 executionTimeout.
COMMAND_ID="$(
  aws ssm send-command \
    --instance-ids "${INSTANCE_ID}" \
    --document-name AWS-RunShellScript \
    --comment "Seonjalal production deployment" \
    --parameters "${SSM_PARAMETERS}" \
    --timeout-seconds 60 \
    --query 'Command.CommandId' \
    --output text
)"

if [[ -z "${COMMAND_ID}" || "${COMMAND_ID}" == "None" ]]; then
  echo "SSM did not return a command ID." >&2
  exit 1
fi

echo "SSM command ID: ${COMMAND_ID}"

INVOCATION=""
STATUS="Pending"

# SSM 상태 폴링. 전송 직후에는 invocation이 아직 없어 조회가 실패할 수 있으므로 실패는 Pending으로
# 간주하고 재시도한다(stderr를 숨기므로 권한 오류도 같은 방식으로 가려진다).
# 종료 상태: Success/Cancelled/TimedOut/Failed/Cancelling. 진행 상태: Pending/InProgress/Delayed.
# 그 외 알 수 없는 상태는 즉시 실패.
for ((attempt = 1; attempt <= SSM_MAX_POLLS; attempt++)); do
  if INVOCATION="$(
    aws ssm get-command-invocation \
      --command-id "${COMMAND_ID}" \
      --instance-id "${INSTANCE_ID}" \
      --output json \
      2>/dev/null
  )"; then
    STATUS="$(jq -r '.Status' <<< "${INVOCATION}")"
  else
    STATUS="Pending"
  fi

  case "${STATUS}" in
    Success|Cancelled|TimedOut|Failed|Cancelling)
      break
      ;;
    Pending|InProgress|Delayed)
      echo "Waiting for SSM command (${attempt}/${SSM_MAX_POLLS}): ${STATUS}"
      sleep "${SSM_POLL_INTERVAL_SECONDS}"
      ;;
    *)
      echo "Unexpected SSM command status: ${STATUS}" >&2
      exit 1
      ;;
  esac
done

# 폴링 한도 내내 invocation을 한 번도 못 얻은 경우(명령 전달 자체가 실패한 경우 등).
if [[ -z "${INVOCATION}" ]]; then
  echo "SSM command invocation was not available before the polling limit." >&2
  exit 1
fi

# 원격 스크립트 출력(헬스 대기, 실패 시 컨테이너 로그 tail 200줄, 롤백 로그)을 Actions 로그에 접어서 표시.
# 컨테이너 로그가 그대로 공개되므로 앱이 Secret을 로그에 찍지 않아야 한다.
echo "::group::SSM standard output"
jq -r '.StandardOutputContent // ""' <<< "${INVOCATION}"
echo "::endgroup::"

if [[ -n "$(jq -r '.StandardErrorContent // ""' <<< "${INVOCATION}")" ]]; then
  echo "::group::SSM standard error"
  jq -r '.StandardErrorContent // ""' <<< "${INVOCATION}" >&2
  echo "::endgroup::"
fi

# Success 외 모든 상태는 실패 처리. 폴링 한도 초과로 InProgress 상태에서 빠져나온 경우도
# 여기서 실패가 되며, 이때 EC2에서는 배포가 계속 진행 중일 수 있다(이 스크립트는 SSM 명령을 취소하지 않음).
if [[ "${STATUS}" != "Success" ]]; then
  echo "Production deployment command finished with status: ${STATUS}" >&2
  exit 1
fi

echo "Production deployment command completed successfully."
