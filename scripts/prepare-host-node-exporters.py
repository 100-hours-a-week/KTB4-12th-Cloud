#!/usr/bin/env python3
"""Mac: 비운영 기존3대 또는 Develop1대의 최초 설치 요청 생성. AWS 호출 없음."""
import argparse
import base64
import json
import os
from pathlib import Path
import tempfile

TARGETS = {
    "i-0c6f5ea09b5e20859": "172.31.6.212",
    "i-09a8f9d32136ed6dc": "10.20.11.100",
    "i-0a715048312bc854d": "10.20.10.236",
}
DEVELOP_TARGETS = {"i-06f8ccdce3a1e9ff4": "10.20.0.28"}
VERSION = "1.12.1"

REMOTE = r'''
import fcntl, hashlib, io, json, os, pathlib, platform, pwd, re, subprocess, tarfile, time, urllib.request

def require(ok, message):
    if not ok:
        raise SystemExit('STOP: ' + message)

def run(*args):
    result = subprocess.run(args, capture_output=True, text=True)
    require(result.returncode == 0, '명령 단계 실패: ' + args[0] + '; 재설치 전 상태 확인 필요')
    return result.stdout.strip()

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
def get(url, limit, headers=None):
    with opener.open(urllib.request.Request(url, headers=headers or {}), timeout=45) as response:
        body = response.read(limit + 1)
    require(len(body) <= limit, '다운로드 크기 상한 초과')
    return body

require(os.geteuid() == 0 and platform.system() == 'Linux' and platform.machine() == 'x86_64',
        'Linux amd64 root 대상 필요')
request = urllib.request.Request('http://169.254.169.254/latest/api/token', method='PUT',
    headers={'X-aws-ec2-metadata-token-ttl-seconds': '60'})
with opener.open(request, timeout=3) as response:
    token = response.read().decode()
identity = json.loads(get('http://169.254.169.254/latest/dynamic/instance-identity/document', 65536,
    {'X-aws-ec2-metadata-token': token}))
targets = __TARGETS__
require(identity.get('accountId') == '721744297924' and identity.get('region') == 'ap-northeast-2'
        and identity.get('instanceId') in targets, '허용된 비운영 대상 아님')
ip = targets[identity['instanceId']]
require(identity.get('privateIp') == ip, '대상 IP 변경: 재조회 필요')
lock_fd = os.open('/run/lock/seonjalal-node-exporter-install.lock',
                  os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
try:
    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    raise SystemExit('STOP: 다른 설치 실행 중. 중복 요청 금지')
root = pathlib.Path('/opt/seonjalal-node-exporter')
unit = pathlib.Path('/etc/systemd/system/seonjalal-node-exporter.service')
require(not root.exists() and not root.is_symlink() and not unit.exists() and not unit.is_symlink(),
        '기존 exporter 경로 보존. 최초 설치만 허용')
require('9100' not in run('ss', '-H', '-lnt', 'sport = :9100'), '9100 포트 기존 점유 확인 필요')
try:
    pwd.getpwnam('seonjalal-node-exporter')
except KeyError:
    pass
else:
    raise SystemExit('STOP: 기존 전용 계정 보존 필요')

version = '__VERSION__'
archive_name = 'node_exporter-' + version + '.linux-amd64.tar.gz'
url = 'https://github.com/prometheus/node_exporter/releases/download/v' + version + '/'
print('NODE_EXPORTER_DOWNLOAD_STARTED INSTANCE=' + identity['instanceId'], flush=True)
checksums = get(url + 'sha256sums.txt', 1024**2).decode()
matches = [line.split()[0] for line in checksums.splitlines()
           if len(line.split()) == 2 and line.split()[1].lstrip('*') == archive_name]
require(len(matches) == 1 and re.fullmatch('[0-9a-f]{64}', matches[0]), '공식 SHA256 목록 확인 필요')
archive = get(url + archive_name, 32 * 1024**2)
require(hashlib.sha256(archive).hexdigest() == matches[0], '아카이브 checksum 불일치')
with tarfile.open(fileobj=io.BytesIO(archive), mode='r:gz') as package:
    # extractall 없이 검증한 단일 파일만 읽음. 경로 탈출·심볼릭 링크 추출 금지.
    member = package.getmember('node_exporter-' + version + '.linux-amd64/node_exporter')
    require(member.isfile() and member.size <= 64 * 1024**2, '바이너리 archive 항목 불일치')
    binary = package.extractfile(member).read()

# 앱/DB/Docker 구성에는 접근하지 않음. 새 계정·바이너리·서비스만 생성.
os.umask(0o022)
root.mkdir(mode=0o755)
executable = root / 'node_exporter'
executable.write_bytes(binary)
os.chmod(executable, 0o755)
version_result = subprocess.run([str(executable), '--version'], capture_output=True, text=True)
require(version_result.returncode == 0 and 'version ' + version in version_result.stdout + version_result.stderr,
        '바이너리 버전 불일치')
run('useradd', '--system', '--user-group', '--no-create-home', '--home-dir', '/nonexistent',
    '--shell', '/sbin/nologin', 'seonjalal-node-exporter')
collectors = ('cpu', 'meminfo', 'filesystem', 'diskstats', 'netdev', 'loadavg',
              'stat', 'time', 'uname', 'vmstat', 'netstat', 'sockstat')
flags = ['--web.listen-address=' + ip + ':9100', '--collector.disable-defaults']
flags += ['--collector.' + name for name in collectors]
unit.write_text('[Unit]\nDescription=Seonjalal nonproduction host metrics\nAfter=network-online.target\n'
    'Wants=network-online.target\n\n[Service]\nType=simple\n'
    'User=seonjalal-node-exporter\nGroup=seonjalal-node-exporter\n'
    'ExecStart=' + str(executable) + ' ' + ' '.join(flags) + '\n'
    'Restart=on-failure\nRestartSec=5\nNoNewPrivileges=true\n'
    'CapabilityBoundingSet=\nRestrictSUIDSGID=true\n'
    'MemoryMax=128M\nCPUQuota=20%\nTasksMax=32\n'
    'Environment=GOMEMLIMIT=96MiB\n\n[Install]\nWantedBy=multi-user.target\n')
os.chmod(unit, 0o644)
run('systemd-analyze', 'verify', str(unit))
record = {'version': version, 'instance': identity['instanceId'], 'private_ip': ip,
          'archive_sha256': matches[0], 'binary_sha256': hashlib.sha256(binary).hexdigest(),
          'note': 'Official HTTPS checksum comparison; no separate signature verification'}
(root / 'install-record.json').write_text(json.dumps(record, indent=2) + '\n')
run('systemctl', 'daemon-reload')
run('systemctl', 'enable', '--now', 'seonjalal-node-exporter.service')
body = ''
for attempt in range(15):
    try:
        body = get('http://' + ip + ':9100/metrics', 8 * 1024**2).decode()
        if all(metric in body for metric in ('node_cpu_seconds_total', 'node_memory_MemAvailable_bytes',
                                             'node_filesystem_avail_bytes', 'node_load1')):
            break
    except OSError:
        pass
    time.sleep(1)
require(all(metric in body for metric in ('node_cpu_seconds_total', 'node_memory_MemAvailable_bytes',
                                         'node_filesystem_avail_bytes', 'node_load1')), '핵심 host metric 확인 필요')
require(run('systemctl', 'is-active', 'seonjalal-node-exporter.service') == 'active', '서비스 활성 확인 필요')
print('NODE_EXPORTER_ACTIVE INSTANCE=' + identity['instanceId'] + ' PRIVATE_IP=' + ip)
print('NODE_EXPORTER_VERSION=' + version)
print('HOST_CPU_MEMORY_DISK_METRICS_VERIFIED')
print('NO_APP_DB_DOCKER_RESTART_OR_SECURITY_GROUP_CHANGE')
print('LIMITATION=Prometheus reachability and scraping pending; not container or RDS metrics')
'''


def build_request(target='baseline'):
    if target not in ('baseline', 'develop'):
        raise ValueError('알 수 없는 설치 대상')
    # 기존3대 재설치와 Develop 신규 설치를 섞지 않고 정확한 대상만 선택.
    selected = DEVELOP_TARGETS if target == 'develop' else TARGETS
    remote = REMOTE.replace('__TARGETS__', repr(selected)).replace('__VERSION__', VERSION)
    compile(remote, '<node-exporter-install>', 'exec')
    encoded = base64.b64encode(remote.encode()).decode()
    command = "python3 - <<'PY'\nimport base64\nexec(compile(base64.b64decode(" + repr(encoded) + "), '<node-exporter-install>', 'exec'))\nPY"
    return {'DocumentName': 'AWS-RunShellScript', 'InstanceIds': list(selected),
            'Comment': 'Install host metrics exporter on selected nonproduction hosts only: ' + target,
            'MaxConcurrency': '1', 'MaxErrors': '0', 'TimeoutSeconds': 600,
            'Parameters': {'commands': [command], 'executionTimeout': ['600']}}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', choices=('baseline', 'develop'), default='baseline')
    args = parser.parse_args()
    directory = Path(tempfile.mkdtemp(prefix='seonjalal-host-exporters-', dir='/private/tmp'))
    os.chmod(directory, 0o700)
    path = directory / 'request.json'
    request = build_request(args.target)
    path.write_text(json.dumps(request))
    os.chmod(path, 0o600)
    print('REQUEST_FILE=' + str(path))
    print('TARGET_COUNT=' + str(len(request['InstanceIds'])) + ' MAX_CONCURRENCY=1 STOP_ON_FIRST_FAILED_TARGET')
    print('PREPARED_ONLY_NO_AWS_CALL_OR_SERVER_CHANGE')
