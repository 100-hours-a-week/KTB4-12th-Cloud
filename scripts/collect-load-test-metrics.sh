#!/usr/bin/env bash

set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "Usage: $0 RUN_DIR [INTERVAL_SECONDS]" >&2
  exit 1
fi

RUN_DIR="$1"
INTERVAL_SECONDS="${2:-5}"
EXPECTED_PREFIX="/opt/seonjalal-develop/load-test-results/"

if [[ "${RUN_DIR}/" != "${EXPECTED_PREFIX}"* ]]; then
  echo "RUN_DIR must be below ${EXPECTED_PREFIX}" >&2
  exit 1
fi

if [[ ! "${INTERVAL_SECONDS}" =~ ^[1-9][0-9]*$ ]]; then
  echo "INTERVAL_SECONDS must be a positive integer." >&2
  exit 1
fi

install -d -m 700 "${RUN_DIR}"

CONTAINER_METRICS="${RUN_DIR}/docker-stats-during.tsv"
HOST_METRICS="${RUN_DIR}/host-vmstat-during.tsv"
COLLECTOR_LOG="${RUN_DIR}/metrics-collector.log"

printf 'timestamp\tcontainer\tcpu_percent\tmemory_usage\tmemory_percent\tnetwork_io\tblock_io\tpids\n' \
  > "${CONTAINER_METRICS}"
printf 'timestamp\trunnable\tblocked\tswap_used\tfree_memory\tbuffer_memory\tcache_memory\tswap_in\tswap_out\tblocks_in\tblocks_out\tinterrupts\tcontext_switches\tcpu_user\tcpu_system\tcpu_idle\tcpu_wait\tcpu_stolen\n' \
  > "${HOST_METRICS}"

stop_requested=false
trap 'stop_requested=true' INT TERM

echo "collector_started_at=$(date --iso-8601=seconds) interval_seconds=${INTERVAL_SECONDS}" \
  > "${COLLECTOR_LOG}"

while [[ "${stop_requested}" == false ]]; do
  timestamp="$(date --iso-8601=seconds)"

  docker stats --no-stream \
    --format "${timestamp}\t{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}\t{{.NetIO}}\t{{.BlockIO}}\t{{.PIDs}}" \
    seonjalal-frontend seonjalal-backend seonjalal-database \
    >> "${CONTAINER_METRICS}" 2>> "${COLLECTOR_LOG}" || true

  vmstat 1 2 | awk -v timestamp="${timestamp}" 'END {
    printf "%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n",
      timestamp, $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17
  }' >> "${HOST_METRICS}" 2>> "${COLLECTOR_LOG}" || true

  sleep "${INTERVAL_SECONDS}" &
  wait $! || true
done

echo "collector_finished_at=$(date --iso-8601=seconds)" >> "${COLLECTOR_LOG}"
