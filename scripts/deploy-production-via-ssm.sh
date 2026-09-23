#!/usr/bin/env bash

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
REMOTE_SCRIPT_PATH="${REMOTE_SCRIPT_PATH:-scripts/deploy-production.sh}"
SSM_EXECUTION_TIMEOUT_SECONDS="${SSM_EXECUTION_TIMEOUT_SECONDS:-900}"
SSM_POLL_INTERVAL_SECONDS="${SSM_POLL_INTERVAL_SECONDS:-5}"
SSM_MAX_POLLS="${SSM_MAX_POLLS:-190}"

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

SCRIPT_PAYLOAD="$(gzip -c "${REMOTE_SCRIPT_PATH}" | base64 | tr -d '\n')"

printf -v DEPLOY_PATH_Q '%q' "${DEPLOY_PATH}"
printf -v FRONTEND_VERSION_Q '%q' "${FRONTEND_VERSION}"
printf -v BACKEND_VERSION_Q '%q' "${BACKEND_VERSION}"
printf -v PUBLIC_BASE_URL_Q '%q' "${PUBLIC_BASE_URL}"
printf -v READ_ONLY_SMOKE_PATH_Q '%q' "${READ_ONLY_SMOKE_PATH}"

REMOTE_WRAPPER="$(cat <<EOF
set -Eeuo pipefail
temporary_script=\"\$(mktemp /tmp/seonjalal-production-deploy.XXXXXX)\"
cleanup() {
  rm -f \"\${temporary_script}\"
}
trap cleanup EXIT
printf '%s' '${SCRIPT_PAYLOAD}' | base64 --decode | gzip --decompress > \"\${temporary_script}\"
chmod 700 \"\${temporary_script}\"
chown ec2-user:ec2-user \"\${temporary_script}\"
runuser -u ec2-user -- \"\${temporary_script}\" ${DEPLOY_PATH_Q} ${FRONTEND_VERSION_Q} ${BACKEND_VERSION_Q} ${PUBLIC_BASE_URL_Q} ${READ_ONLY_SMOKE_PATH_Q}
EOF
)"

printf -v REMOTE_WRAPPER_Q '%q' "${REMOTE_WRAPPER}"
SSM_COMMAND="bash -lc ${REMOTE_WRAPPER_Q}"
SSM_PARAMETERS="$(
  jq -cn \
    --arg command "${SSM_COMMAND}" \
    --arg execution_timeout "${SSM_EXECUTION_TIMEOUT_SECONDS}" \
    '{commands: [$command], executionTimeout: [$execution_timeout]}'
)"

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

if [[ -z "${INVOCATION}" ]]; then
  echo "SSM command invocation was not available before the polling limit." >&2
  exit 1
fi

echo "::group::SSM standard output"
jq -r '.StandardOutputContent // ""' <<< "${INVOCATION}"
echo "::endgroup::"

if [[ -n "$(jq -r '.StandardErrorContent // ""' <<< "${INVOCATION}")" ]]; then
  echo "::group::SSM standard error"
  jq -r '.StandardErrorContent // ""' <<< "${INVOCATION}" >&2
  echo "::endgroup::"
fi

if [[ "${STATUS}" != "Success" ]]; then
  echo "Production deployment command finished with status: ${STATUS}" >&2
  exit 1
fi

echo "Production deployment command completed successfully."
