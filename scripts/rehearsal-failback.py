#!/usr/bin/env python3
"""격리된 V1 Benchmark 전용. prepare는 검사만, 실제 변경은 명시적 단계로 분리."""
import argparse
import fcntl
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

WORK = Path('/var/tmp/seonjalal-failback.cmDJTi')
ACTIVE = Path('/opt/seonjalal-develop/nginx/conf.d/default.conf')
TARGET = 'seonjalal-v2-migration-rehearsal.cbqckkusw4ke.ap-northeast-2.rds.amazonaws.com'
SECRET = Path('/var/tmp/seonjalal-dms-target.mSlDHw/password')
CA = '/tmp/seonjalal-rds-ca.M1b7FF/rds-ca.pem'
SOURCE_APP = 'seonjalal-backend'
TARGET_APP = 'seonjalal-backend-cutover-target'
TABLES = sorted('categories flyway_schema_history friends gift_histories '
                'login_email_failure_limits login_ip_rate_limits product_images '
                'product_views products recipient_profiles recipient_recommended_products '
                'term_consents terms user_dislike_categories user_sessions users'.split())


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def run(args, **kwargs):
    # 비밀번호를 명령행에 넣지 않고 컨테이너 표준입력으로 전달.
    return subprocess.run(args, check=True, timeout=kwargs.pop('timeout', 120), **kwargs)


def output(args):
    return run(args, capture_output=True, text=True).stdout


def event(name, **details):
    row = dict(event=name, utc=datetime.now(timezone.utc).isoformat(),
               monotonic_ns=time.monotonic_ns(), **details)
    with (WORK / 'failback-events.jsonl').open('a') as stream:
        stream.write(json.dumps(row) + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    print(name.upper() + '_UTC=' + row['utc'], flush=True)


def history():
    path = WORK / 'failback-events.jsonl'
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def source(sql):
    return output(['docker', 'exec', 'seonjalal-database', 'sh', '-c',
                   'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql --user=root '
                   '--protocol=SOCKET --batch --skip-column-names '
                   '--database=seonjalal_develop --execute="$1"', 'sh', sql])


def target(sql):
    with SECRET.open('rb') as stream:
        return run(['docker', 'exec', '-i', 'seonjalal-database', 'sh', '-c',
                    'set -eu; IFS= read -r password; MYSQL_PWD="$password" exec mysql '
                    '--host="$1" --port=3306 --user=dms_rehearsal '
                    '--ssl-mode=VERIFY_IDENTITY --ssl-ca="$2" '
                    '--batch --skip-column-names --database=seonjalal_develop '
                    '--execute="$3"', 'sh', TARGET, CA, sql],
                   stdin=stream, stdout=subprocess.PIPE, text=True).stdout


def config(name):
    content = (WORK / name).read_bytes()
    hashes = json.loads((WORK / 'prepared-config-hashes.json').read_text())
    require(hashlib.sha256(content).hexdigest() == hashes[name], '설정 해시 불일치: ' + name)
    return content


def inspect_apps():
    apps = json.loads(output(['docker', 'inspect', SOURCE_APP, TARGET_APP]))
    for app, host in zip(apps, ['database', TARGET]):
        env = dict(item.split('=', 1) for item in app['Config']['Env'] if '=' in item)
        require(app['State'].get('Health', {}).get('Status') == 'healthy', 'Backend Health 확인 필요')
        url = urlsplit(env.get('DB_URL', '').removeprefix('jdbc:'))
        require(url.hostname == host and url.path == '/seonjalal_develop', 'DB 연결 대상 불일치')
        for key in ['SCHEDULING_ENABLED', 'AI_PROFILE_SCHEDULING_ENABLED']:
            require(env.get(key) == 'false', '자동 Writer 설정 확인 필요')
        require(not any(app['HostConfig'].get('PortBindings', {}).values()), 'Backend 직접 노출 확인 필요')
    require(apps[0]['Image'] == apps[1]['Image'], '두 Backend Image ID 불일치')
    require(apps[1]['HostConfig']['RestartPolicy']['Name'] == 'no', '후보 자동 재시작 설정 확인 필요')


def fence_check():
    require(source('SELECT @@GLOBAL.read_only, @@GLOBAL.super_read_only;').strip() == '1\t1',
            'Source 쓰기 Fence 확인 필요')
    require(source('SELECT COUNT(*) FROM information_schema.innodb_trx;').strip() == '0',
            'Source 진행 중 Transaction 존재')


def backup_check():
    backups = {
        'rds-preflight.sql': '6709e4c827ec6d7ba14b53ab84593cf7101300b95ff9cda06b1ef4fbec300f03',
        'source-before-failback.sql': '80f69f383651182184ef57d103233afd7f4e1bf1b32ef35d6da6e6609ccd8b4c',
    }
    for name, expected in backups.items():
        require(hashlib.sha256((WORK / name).read_bytes()).hexdigest() == expected,
                '사전 백업 해시 불일치: ' + name)


def inventory(query):
    rows = query("SELECT TABLE_NAME, ENGINE FROM information_schema.tables "
                 "WHERE TABLE_SCHEMA=DATABASE() ORDER BY TABLE_NAME;").strip().splitlines()
    require(rows == [name + '\tInnoDB' for name in TABLES], '예상한 InnoDB 16개 테이블과 다름')
    objects = query("SELECT (SELECT COUNT(*) FROM information_schema.triggers WHERE TRIGGER_SCHEMA=DATABASE()),"
                    "(SELECT COUNT(*) FROM information_schema.routines WHERE ROUTINE_SCHEMA=DATABASE()),"
                    "(SELECT COUNT(*) FROM information_schema.events WHERE EVENT_SCHEMA=DATABASE());").strip()
    require(objects == '0\t0\t0', 'Dump 범위 밖 DB 객체 확인 필요')


def workers():
    lines = output(['docker', 'exec', 'seonjalal-frontend', 'ps', '-o', 'pid,args']).splitlines()
    return {line.split()[0] for line in lines if 'nginx: worker process' in line}


def write_active(content):
    # 단일 파일 Bind Mount이므로 inode를 교체하지 않고 내용만 수정.
    with ACTIVE.open('r+b') as stream:
        stream.write(content)
        stream.truncate()
        stream.flush()
        os.fsync(stream.fileno())


def reload_to(expected, desired, name):
    require(ACTIVE.read_bytes() == config(expected), '현재 Nginx 설정 불일치')
    old = workers()
    require(old, 'Nginx Worker 확인 실패')
    write_active(config(desired))
    try:
        run(['docker', 'exec', 'seonjalal-frontend', 'nginx', '-t'])
    except Exception:
        write_active(config(expected))
        raise
    run(['docker', 'exec', 'seonjalal-frontend', 'nginx', '-s', 'reload'])
    event(name)
    deadline = time.monotonic() + 60
    while old & workers():
        require(time.monotonic() < deadline, '이전 Worker Drain 시간 초과; 차단 유지')
        time.sleep(0.25)
    event(name + '_drained')


def read_probe(app):
    run(['docker', 'exec', app, 'curl', '-fsS', '--max-time', '10', '-o', '/dev/null',
         'http://frontend/api/products?sort=POPULAR'])


def frozen_probe():
    value = output(['docker', 'exec', SOURCE_APP, 'curl', '-sS', '--max-time', '10',
                    '-X', 'POST', '-w', '\n%{http_code}',
                    'http://frontend/api/__freeze_probe__'])
    body, status = value.rsplit('\n', 1)
    require(status == '503' and json.loads(body)['error']['code'] == 'MIGRATION_WRITE_FROZEN',
            '쓰기 Gate 응답 확인 실패')


def checksums(query):
    sql = 'CHECKSUM TABLE ' + ','.join('`seonjalal_develop`.`' + name + '`' for name in TABLES) + ' EXTENDED;'
    result = query(sql)
    rows = dict(line.split('\t') for line in result.strip().splitlines())
    require(set(rows) == {'seonjalal_develop.' + name for name in TABLES}, '체크섬 대상 누락')
    require(all(value.isdigit() for value in rows.values()), 'NULL/비정상 체크섬')
    return result


def prepare():
    require(not history(), '이전 Failback 실행 기록 존재; 자동 재실행 금지')
    backup_check()
    inspect_apps()
    fence_check()
    inventory(source)
    inventory(target)
    require(ACTIVE.read_bytes() == config('nginx.before-failback.conf'), '현재 설정 변경 확인 필요')
    for name in ['nginx.rds-frozen.conf', 'nginx.source-frozen.conf', 'nginx.source-open.conf']:
        config(name)
    # 별도 Nginx 구성으로 문법 검사. 실제 설정/reload 변경 없음.
    remote = output(['docker', 'exec', 'seonjalal-frontend', 'mktemp', '-d', '/tmp/failback-check.XXXXXX']).strip()
    for name in ['nginx.rds-frozen.conf', 'nginx.source-frozen.conf', 'nginx.source-open.conf']:
        run(['docker', 'cp', str(WORK / name), 'seonjalal-frontend:' + remote + '/' + name])
        wrapper = ('events {}\nhttp { include ' + remote + '/' + name + '; }\n').encode()
        run(['docker', 'exec', '-i', 'seonjalal-frontend', 'sh', '-c',
             'umask 077; cat > "$1"', 'sh', remote + '/validation.conf'], input=wrapper)
        run(['docker', 'exec', 'seonjalal-frontend', 'nginx', '-t', '-c', remote + '/validation.conf'])
    read_probe(SOURCE_APP)
    print('RDS_SMOKE_AND_STOCK=' + target(
        'SELECT id,product_id,quantity,product_price_snapshot,HEX(idempotency_key) '
        'FROM gift_histories WHERE id=5010; SELECT quantity FROM products WHERE id=4;').strip())
    print('FAILBACK_PREPARE_OK_NO_TRAFFIC_CHANGE')


def freeze_restore():
    require(not history(), '실행 기록 존재; 재실행 금지')
    backup_check()
    inspect_apps()
    fence_check()
    inventory(source)
    inventory(target)
    require(ACTIVE.read_bytes() == config('nginx.before-failback.conf'), 'RDS 정상 설정 확인 필요')
    event('freeze_requested')
    reload_to('nginx.before-failback.conf', 'nginx.rds-frozen.conf', 'rds_freeze_reloaded')
    read_probe(SOURCE_APP)
    frozen_probe()
    # 기존 요청 Drain 및 스케줄러/직접 노출 검사로 격리된 업무 Writer 중지.
    # 외부 관리자/다른 Writer가 없는 전용 리허설 DB가 전제.
    event('final_dump_started')
    dump = WORK / 'rds-final.sql'
    with SECRET.open('rb') as password, dump.open('xb') as stream:
        run(['docker', 'exec', '-i', 'seonjalal-database', 'sh', '-c',
             'set -eu; IFS= read -r password; MYSQL_PWD="$password" exec mysqldump '
             '--host="$1" --port=3306 --user=dms_rehearsal --ssl-mode=VERIFY_IDENTITY '
             '--ssl-ca="$2" --single-transaction --skip-lock-tables --no-tablespaces '
             '--set-gtid-purged=OFF --skip-triggers --hex-blob seonjalal_develop',
             'sh', TARGET, CA], stdin=password, stdout=stream, timeout=180)
    require(dump.stat().st_size > 0, '최종 Dump 비어 있음')
    event('final_dump_completed', bytes=dump.stat().st_size,
          sha256=hashlib.sha256(dump.read_bytes()).hexdigest())
    before = checksums(target)
    (WORK / 'rds-final-checksums.tsv').write_text(before)
    event('source_restore_started')
    try:
        source('SET GLOBAL super_read_only=OFF; SET GLOBAL read_only=OFF;')
        with dump.open('rb') as stream:
            run(['docker', 'exec', '-i', 'seonjalal-database', 'sh', '-c',
                 'test "$MYSQL_DATABASE" = seonjalal_develop || exit 1; '
                 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql --user=root '
                 '--protocol=SOCKET --database=seonjalal_develop'], stdin=stream, timeout=180)
    finally:
        # 실패하더라도 Source 쓰기를 다시 차단. 요청은 RDS 읽기 유지/쓰기 거절.
        source('SET GLOBAL read_only=ON; SET GLOBAL super_read_only=ON;')
    fence_check()
    inventory(source)
    restored = checksums(source)
    after = checksums(target)
    (WORK / 'source-restored-checksums.tsv').write_text(restored)
    require(before == restored == after, '최종 데이터 불일치; Freeze 유지, 전환 금지')
    smoke = 'SELECT id,product_id,quantity,product_price_snapshot,HEX(idempotency_key) FROM gift_histories WHERE id=5010;'
    require(source(smoke) == target(smoke) and source(smoke).strip(), 'Smoke 원장 대조 실패')
    (WORK / 'source-restored-binlog.tsv').write_text(source('SHOW BINARY LOG STATUS;'))
    event('restore_verified', tables=16)
    print('RESTORE_VERIFIED_WRITES_STILL_FROZEN')


def switch_reopen():
    records = history()
    require(records and records[-1]['event'] == 'restore_verified', '복원 검증 기록 확인 필요')
    inspect_apps()
    fence_check()
    require(source('SHOW BINARY LOG STATUS;') == (WORK / 'source-restored-binlog.tsv').read_text(),
            '복원 검증 이후 Source 변경 확인 필요')
    require(checksums(source) == checksums(target), '전환 직전 데이터 불일치')
    reload_to('nginx.rds-frozen.conf', 'nginx.source-frozen.conf', 'source_read_reloaded')
    read_probe(TARGET_APP)
    frozen_probe()
    # 읽기 경로를 Source로 옮긴 뒤 이전 RDS 앱을 멈춰 이중 Writer 방지.
    event('target_backend_stop_requested')
    run(['docker', 'stop', '--time', '60', TARGET_APP], timeout=90)
    require(output(['docker', 'inspect', TARGET_APP, '--format', '{{.State.Running}}']).strip() == 'false',
            'RDS Backend 정지 확인 실패')
    event('target_backend_stopped')
    require(checksums(source) == checksums(target), 'Writer 정지 후 최종 데이터 불일치')
    # 여기부터 Source 신규 Commit 가능. RDS 단순 복귀/abort 금지.
    event('source_write_enable_requested')
    source('SET GLOBAL super_read_only=OFF; SET GLOBAL read_only=OFF;')
    require(source('SELECT @@GLOBAL.read_only, @@GLOBAL.super_read_only;').strip() == '0\t0',
            'Source 쓰기 재개 확인 실패')
    event('source_write_enabled')
    reload_to('nginx.source-frozen.conf', 'nginx.source-open.conf', 'source_write_reloaded')
    read_probe(SOURCE_APP)
    event('failback_control_completed')
    records = history()
    seconds = (records[-1]['monotonic_ns'] - records[0]['monotonic_ns']) / 1e9
    print('FAILBACK_CONTROL_SECONDS=' + str(round(seconds, 3)))
    print('FAILBACK_CONTROL_COMPLETED_VERIFY_K6_AND_LEDGER')


def abort_to_rds():
    records = history()
    require(records and not any(row['event'] in ['target_backend_stop_requested',
            'source_write_enable_requested'] for row in records), '자동 RDS 복귀 허용 경계 초과')
    inspect_apps()
    fence_check()
    current = ACTIVE.read_bytes()
    choices = ['nginx.rds-frozen.conf', 'nginx.source-frozen.conf']
    name = next((name for name in choices if current == config(name)), None)
    require(name is not None, '알 수 없는 Nginx 상태; 자동 복귀 금지')
    reload_to(name, 'nginx.before-failback.conf', 'abort_rds_reopened')
    read_probe(SOURCE_APP)
    event('failback_aborted_rds_retained')
    print('RDS_ROUTE_REOPENED_SOURCE_FENCED')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=['prepare', 'freeze-restore', 'switch-reopen', 'abort-to-rds'])
    parser.add_argument('--approved-rehearsal-change', action='store_true')
    args = parser.parse_args()
    os.umask(0o077)
    require(os.geteuid() == 0, 'sudo 필요')
    require(args.phase == 'prepare' or args.approved_rehearsal_change, '실제 변경 단계는 별도 승인 필요')
    with (WORK / 'failback.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        actions = {'prepare': prepare, 'freeze-restore': freeze_restore,
                   'switch-reopen': switch_reopen, 'abort-to-rds': abort_to_rds}
        actions[args.phase]()


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('STOP: ' + str(exc) + '\n자동 재실행/쓰기 해제 금지. 현재 출력 공유 후 상태 확인 필요.', file=sys.stderr)
        sys.exit(1)
