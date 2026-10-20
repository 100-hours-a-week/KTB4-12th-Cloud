#!/usr/bin/env bash
# V1 벤치마크 EC2 전용. DB/설정 변경 없이 자원·누적 상태만 읽고 임시 로그를 만든다.
set -euo pipefail
[[ "$(id -u)" == 0 ]] || { echo 'STOP: sudo로 실행 필요' >&2; exit 1; }
expected='sha256:b93ca625008ea5eb57e600dc56e033a7ca45bce8842ac76b789eb0da6563e1f2'
[[ "$(docker inspect --format '{{.Image}}' seonjalal-backend)" == "$expected" ]] || exit 1
umask 077
monitor_log=$(mktemp /var/tmp/seonjalal-v1-write-monitor.XXXXXXXX)
echo "MONITOR_LOG=$monitor_log"
echo 'MONITOR_STARTED: 별도 Mac 터미널에서 시험 시작; 이 모니터는 최대 8분 후 종료'
monitor_deadline=$((SECONDS + 480))
while (( SECONDS < monitor_deadline )); do
  {
    date -u '+SAMPLE_UTC=%Y-%m-%dT%H:%M:%SZ'
    docker stats --no-stream --format '{{json .}}' \
      seonjalal-backend seonjalal-database seonjalal-frontend
    free -m
    docker exec seonjalal-database sh -c '
      test -n "$MYSQL_ROOT_PASSWORD" || exit 1
      export MYSQL_PWD="$MYSQL_ROOT_PASSWORD"
      exec mysql --user=root --batch --execute="SHOW GLOBAL STATUS WHERE Variable_name IN (
        '\''Threads_connected'\'', '\''Threads_running'\'', '\''Questions'\'', '\''Slow_queries'\'',
        '\''Innodb_row_lock_waits'\'', '\''Innodb_row_lock_time'\'');"
    '
  } | tee -a "$monitor_log"
  sleep 5
done
echo "MONITOR_FINISHED_LOG=$monitor_log"
