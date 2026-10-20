#!/usr/bin/env python3
"""Mac: 고정 시험 리소스 CloudWatch 수집 요청 생성. AWS 호출·원격 변경 없음."""
import base64
import json
import os
from pathlib import Path
import tempfile

VERSION = '0.68.0'
ALB = 'app/seonjalal-v2-staging-alb/2c8c37643585076a'
INSTANCES = {'v1-benchmark': 'i-0c6f5ea09b5e20859', 'v2-ecs-a': 'i-0a715048312bc854d',
             'v2-ecs-c': 'i-09a8f9d32136ed6dc', 'monitor': 'i-0c6a75f630436591c'}


def metric(name, statistic='Average', period=60):
    return {'name': name, 'statistics': [statistic], 'period': period, 'length': 900,
            'nilToZero': False, 'addCloudwatchTimestamp': True}


def configuration():
    jobs = [{'name': 'rehearsal-rds', 'namespace': 'AWS/RDS', 'regions': ['ap-northeast-2'],
             'dimensions': [{'name': 'DBInstanceIdentifier', 'value': 'seonjalal-v2-migration-rehearsal'}],
             'metrics': [metric(name) for name in ('CPUUtilization', 'FreeableMemory', 'DatabaseConnections',
                                                  'FreeStorageSpace', 'ReadLatency', 'WriteLatency',
                                                  'DiskQueueDepth', 'ReadIOPS', 'WriteIOPS')]
                        + [metric('CPUCreditBalance', period=300)]},
            {'name': 'staging-alb', 'namespace': 'AWS/ApplicationELB', 'regions': ['ap-northeast-2'],
             'dimensions': [{'name': 'LoadBalancer', 'value': ALB}],
             'metrics': [metric('RequestCount', 'Sum'), metric('TargetResponseTime'),
                         metric('HTTPCode_ELB_5XX_Count', 'Sum'),
                         metric('HTTPCode_Target_5XX_Count', 'Sum'), metric('TargetConnectionErrorCount', 'Sum')]}]
    for name, instance in INSTANCES.items():
        jobs.append({'name': name, 'namespace': 'AWS/EC2', 'regions': ['ap-northeast-2'],
                     'dimensions': [{'name': 'InstanceId', 'value': instance}],
                     'metrics': [metric('CPUCreditBalance', period=300),
                                 metric('CPUSurplusCreditBalance', period=300),
                                 metric('CPUSurplusCreditsCharged', 'Sum', period=300)]})
    # Static GetMetricStatistics에서 Average는 조회구간 전체 평균으로 이관됨.
    # 시계열 패널은 최신 CW 구간의 Maximum/Minimum을 명시적으로 사용.
    # 순수 percentile-only 요청은 upstream의 Statistics[0] panic을 유발하므로 제외.
    for job in jobs:
        for item in job['metrics']:
            if item['statistics'] == ['Average']:
                extreme = 'Minimum' if item['name'] in ('FreeableMemory', 'FreeStorageSpace', 'CPUCreditBalance', 'CPUSurplusCreditBalance') else 'Maximum'
                item['statistics'].append(extreme)
    return {'apiVersion': 'v1alpha1', 'sts-region': 'ap-northeast-2', 'static': jobs}


REMOTE = r'''
import fcntl, hashlib, io, ipaddress, json, math, os, pathlib, platform, pwd, re, subprocess, tarfile, tempfile, time, urllib.request

def require(ok, message):
    if not ok:
        raise SystemExit('STOP: ' + message)

def run(*args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=90)
    require(result.returncode == 0, '명령 실패: ' + args[0] + '; 재전송 전 상태 확인 필요')
    return result.stdout.strip()

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
def fetch(url, limit=8 * 1024**2):
    with opener.open(urllib.request.Request(url, headers={'User-Agent': 'seonjalal-observability'}), timeout=45) as response:
        body = response.read(limit + 1)
    require(len(body) <= limit, '응답 크기 상한 초과')
    return body

def api(url):
    return json.loads(fetch(url))

require(os.geteuid() == 0 and platform.machine() == 'x86_64', 'amd64 root 필요')
request = urllib.request.Request('http://169.254.169.254/latest/api/token', method='PUT',
    headers={'X-aws-ec2-metadata-token-ttl-seconds': '60'})
with opener.open(request, timeout=3) as response:
    token = response.read().decode()
request = urllib.request.Request('http://169.254.169.254/latest/dynamic/instance-identity/document',
    headers={'X-aws-ec2-metadata-token': token})
with opener.open(request, timeout=3) as response:
    identity = json.load(response)
require((identity.get('accountId'), identity.get('region'), identity.get('instanceId')) ==
        ('721744297924', 'ap-northeast-2', 'i-0c6a75f630436591c'), '관측 노드 불일치')
lock_fd = os.open('/run/lock/seonjalal-observability-install.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
try:
    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    raise SystemExit('STOP: 다른 관측 변경 실행 중. 중복 요청 금지')
root = pathlib.Path('/opt/seonjalal-cloudwatch-exporter')
monitor = pathlib.Path('/opt/seonjalal-observability')
unit = pathlib.Path('/etc/systemd/system/seonjalal-cloudwatch-exporter.service')
dashboard_path = monitor / 'config/provisioning/dashboards/files/seonjalal-cloudwatch.json'
require(not root.exists() and not root.is_symlink() and not unit.exists() and not unit.is_symlink(),
        '기존 CloudWatch 설치 경로 보존 필요')
require(not dashboard_path.exists() and not dashboard_path.is_symlink(), '기존 대시보드 보존 필요')
require(dashboard_path.parent.is_dir() and not dashboard_path.parent.is_symlink(), '검증된 host dashboard 경로 필요')
try:
    pwd.getpwnam('seonjalal-cloudwatch')
except KeyError:
    pass
else:
    raise SystemExit('STOP: 기존 서비스 계정 보존 필요')
require(not run('ss', '-H', '-lnt', 'sport = :5000'), '기존5000포트 확인 필요')
network = json.loads(run('docker', 'network', 'inspect', 'seonjalal-monitor'))[0]
require(network['Driver'] == 'bridge' and len(network['IPAM']['Config']) == 1, '전용 Docker bridge 확인 필요')
gateway = network['IPAM']['Config'][0]['Gateway']
require(ipaddress.ip_address(gateway).is_private and ipaddress.ip_address(gateway) in
        ipaddress.ip_network(network['IPAM']['Config'][0]['Subnet']), '전용 bridge gateway 확인 필요')
prom = json.loads(run('docker', 'inspect', 'seonjalal-monitor-prometheus'))[0]
require(prom['State']['Running'], 'Prometheus 정상 기동 필요')
prom_path = monitor / 'config/prometheus.yml'
require(prom_path.is_file() and not prom_path.is_symlink(), '기존 수집 설정 경로 확인 필요')
old = prom_path.read_bytes()
prom_config = json.loads(old)
require({job['job_name'] for job in prom_config['scrape_configs']} == {'prometheus', 'node_exporter'},
        '기존 host 수집 계약 불일치: 변경 보존 후 검토 필요')
require(sum(len(group['targets']) for job in prom_config['scrape_configs'] if job['job_name'] == 'node_exporter'
            for group in job['static_configs']) == 3, '호스트3개 유지 필요')
backup = pathlib.Path(tempfile.mkdtemp(prefix='before-cloudwatch-', dir=monitor))
os.chmod(backup, 0o700)
(backup / 'prometheus.yml').write_bytes(old)

# 고정 release API에서 linux-amd64 archive 및 checksum 이름을 확인. 모호하면 변경 전 중단.
version = '__VERSION__'
repository = 'https://github.com/prometheus-community/yet-another-cloudwatch-exporter/releases/download/v' + version + '/'
release = api('https://api.github.com/repos/prometheus-community/yet-another-cloudwatch-exporter/releases/tags/v' + version)
require(release.get('tag_name') == 'v' + version and not release.get('prerelease'), '고정 release 확인 필요')
archives = [a for a in release['assets'] if re.search(r'linux[-_]amd64\.tar\.gz$', a['name'])]
checksums = [a for a in release['assets'] if ('sha256' in a['name'].lower() or 'checksums' in a['name'].lower())
             and a['name'].endswith('.txt')]
require(len(archives) == 1 and len(checksums) == 1, 'release archive/checksum 항목 확인 필요')
for asset in (archives[0], checksums[0]):
    require(asset['browser_download_url'].startswith(repository), '공식 고정 release URL 필요')
sums = fetch(checksums[0]['browser_download_url'], 1024**2).decode()
expected = [line.split()[0] for line in sums.splitlines() if len(line.split()) == 2 and
            line.split()[1].lstrip('*') == archives[0]['name']]
require(len(expected) == 1 and re.fullmatch('[0-9a-f]{64}', expected[0]), 'SHA256 항목 확인 필요')
payload = fetch(archives[0]['browser_download_url'], 64 * 1024**2)
require(hashlib.sha256(payload).hexdigest() == expected[0], 'release checksum 불일치')
with tarfile.open(fileobj=io.BytesIO(payload), mode='r:gz') as archive:
    members = [m for m in archive.getmembers() if m.isfile() and
               pathlib.PurePosixPath(m.name).name in ('yace', 'yet-another-cloudwatch-exporter')]
    require(len(members) == 1 and members[0].size <= 128 * 1024**2, '단일 YACE 바이너리 확인 필요')
    binary = archive.extractfile(members[0]).read()
os.umask(0o022)
root.mkdir(mode=0o755)
executable = root / 'yace'
executable.write_bytes(binary)
os.chmod(executable, 0o755)
require(version in run(str(executable), 'version'), 'YACE 버전 불일치')
config_path = root / 'config.yml'
config_path.write_text(json.dumps(__CONFIG__, indent=2) + '\n')
os.chmod(config_path, 0o644)
run(str(executable), 'verify-config', '--config.file=' + str(config_path))
run('useradd', '--system', '--user-group', '--no-create-home', '--home-dir', '/nonexistent',
    '--shell', '/sbin/nologin', 'seonjalal-cloudwatch')
unit.write_text('[Unit]\nDescription=Seonjalal fixed CloudWatch metric collector\n'
    'After=network-online.target docker.service\nRequires=docker.service\n\n[Service]\n'
    'User=seonjalal-cloudwatch\nGroup=seonjalal-cloudwatch\n'
    'ExecStart=' + str(executable) + ' --config.file=' + str(config_path) +
    ' --listen-address=' + gateway + ':5000 --disable-account-alias-lookup --scraping-interval=60'
    ' --cloudwatch-concurrency=2 --metrics-per-query=100\n'
    'Environment=AWS_REGION=ap-northeast-2\nEnvironment=AWS_DEFAULT_REGION=ap-northeast-2\n'
    'Environment=GOMEMLIMIT=144MiB\nNoNewPrivileges=true\nCapabilityBoundingSet=\n'
    'Restart=on-failure\nRestartSec=10\nMemoryMax=192M\nCPUQuota=25%\nTasksMax=64\n'
    '\n[Install]\nWantedBy=multi-user.target\n')
os.chmod(unit, 0o644)
run('systemd-analyze', 'verify', str(unit))
(root / 'install-record.json').write_text(json.dumps({'version': version, 'sha256': expected[0],
    'listen': gateway + ':5000', 'source': repository,
    'note': 'Official HTTPS checksum comparison, not independent signature verification'}, indent=2) + '\n')
run('systemctl', 'daemon-reload')
run('systemctl', 'enable', '--now', 'seonjalal-cloudwatch-exporter.service')

def same_inode(data):
    with prom_path.open('r+b') as stream:
        stream.write(data)
        stream.truncate()
        stream.flush()
        os.fsync(stream.fileno())

changed = False
try:
    # HTTP 응답만으로 성공 처리하지 않고 실제 RDS·EC2 지표의 유한값을 확인.
    exported = ''
    def normalized(value):
        return re.sub('[^a-z0-9]', '', value.lower())
    values = {}
    for attempt in range(75):
        try:
            exported = fetch('http://' + gateway + ':5000/metrics').decode()
            values = {}
            for line in exported.splitlines():
                if not line or line.startswith('#'):
                    continue
                fields = line.split()
                if len(fields) < 2:
                    continue
                name = fields[0].split('{')[0]
                try:
                    value = float(fields[1])
                except ValueError:
                    continue
                if math.isfinite(value):
                    values[normalized(name)] = name
            if 'awsrdscpuutilizationaverage' in values and 'awsec2cpucreditbalanceaverage' in values:
                break
        except OSError:
            pass
        time.sleep(2)
    require('awsrdscpuutilizationaverage' in values and 'awsec2cpucreditbalanceaverage' in values,
            'RDS/CPU credit 실제 값 미확인; IAM/네트워크/서비스 로그 진단 필요')
    prom_config['scrape_configs'].append({'job_name': 'cloudwatch', 'scrape_interval': '60s',
        'scrape_timeout': '10s', 'honor_timestamps': True, 'sample_limit': 5000,
        'static_configs': [{'targets': [gateway + ':5000']} ]})
    candidate = backup / 'candidate.yml'
    new = (json.dumps(prom_config, indent=2) + '\n').encode()
    candidate.write_bytes(new)
    os.chmod(candidate, 0o644)
    run('docker', 'run', '--rm', '--network', 'none', '--memory', '256m', '--memory-swap', '256m',
        '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true', '-v', str(candidate) + ':/candidate.yml:ro',
        '--entrypoint', '/bin/promtool', prom['Image'], 'check', 'config', '/candidate.yml')
    changed = True
    same_inode(new)
    run('docker', 'kill', '--signal=HUP', 'seonjalal-monitor-prometheus')
    ready = False
    for attempt in range(45):
        targets = api('http://127.0.0.1:9090/api/v1/targets')['data']['activeTargets']
        cw = [t for t in targets if t.get('labels', {}).get('job') == 'cloudwatch']
        nodes = [t for t in targets if t.get('labels', {}).get('job') == 'node_exporter']
        if len(cw) == 1 and cw[0].get('health') == 'up' and len(nodes) == 3 and all(t['health'] == 'up' for t in nodes):
            ready = True
            break
        time.sleep(2)
    require(ready, 'CloudWatch/기존 host 수집 UP 확인 필요')
except BaseException:
    if changed:
        same_inode(old)
        run('docker', 'kill', '--signal=HUP', 'seonjalal-monitor-prometheus')
        print('PREVIOUS_PROMETHEUS_CONFIG_RESTORED')
    run('systemctl', 'stop', 'seonjalal-cloudwatch-exporter.service')
    print('NEW_CLOUDWATCH_COLLECTOR_STOPPED_FILES_PRESERVED')
    raise

# metric 이름은 검증한 exporter 출력으로 결정. 없는 metric은 정확한 기본 이름으로 No data 유지.
def metric_name(namespace, name, statistic):
    key = normalized(namespace + name + statistic)
    return values.get(key, re.sub('[^a-z0-9_]', '_', namespace.lower()) + '_' + name.lower() + '_' + statistic.lower())

panels = []
definitions = [
    ('RDS CPU % (latest 60s maximum)', 'AWS/RDS', 'CPUUtilization', 'Maximum', 'percent', ''),
    ('RDS free memory (latest 60s minimum)', 'AWS/RDS', 'FreeableMemory', 'Minimum', 'bytes', ''),
    ('RDS connections (latest 60s maximum)', 'AWS/RDS', 'DatabaseConnections', 'Maximum', 'short', ''),
    ('RDS disk queue (latest 60s maximum)', 'AWS/RDS', 'DiskQueueDepth', 'Maximum', 'short', ''),
    ('RDS read latency (latest 60s maximum)', 'AWS/RDS', 'ReadLatency', 'Maximum', 's', ''),
    ('RDS write latency (latest 60s maximum)', 'AWS/RDS', 'WriteLatency', 'Maximum', 's', ''),
    ('ALB target response (latest 60s maximum, NOT p95)', 'AWS/ApplicationELB', 'TargetResponseTime', 'Maximum', 's', ''),
    ('ALB requests / 60s bucket (not cumulative counter)', 'AWS/ApplicationELB', 'RequestCount', 'Sum', 'short', ''),
    ('ALB target 5xx / 60s (No data is not zero)', 'AWS/ApplicationELB', 'HTTPCode_Target_5XX_Count', 'Sum', 'short', ''),
    ('ALB 5xx / 60s (No data is not zero)', 'AWS/ApplicationELB', 'HTTPCode_ELB_5XX_Count', 'Sum', 'short', ''),
    ('EC2 CPU credits (latest 5m minimum, last 15m point)', 'AWS/EC2', 'CPUCreditBalance', 'Minimum', 'short', 'last'),
    ('RDS CPU credits (latest 5m minimum, last 15m point)', 'AWS/RDS', 'CPUCreditBalance', 'Minimum', 'short', 'last')]
for index, (title, namespace, name, statistic, unit_name, transform) in enumerate(definitions):
    query = metric_name(namespace, name, statistic) + '{job="cloudwatch"}'
    if transform == 'last':
        query = 'last_over_time(' + query + '[15m])'
    panels.append({'id': index + 1, 'type': 'timeseries', 'title': title,
        'datasource': {'type': 'prometheus', 'uid': 'seonjalal-prometheus'},
        'gridPos': {'x': 12 * (index % 2), 'y': 8 * (index // 2), 'w': 12, 'h': 8},
        'targets': [{'refId': 'A', 'expr': query, 'legendFormat': '{{name}}'}],
        'fieldConfig': {'defaults': {'unit': unit_name}, 'overrides': []}})
dashboard = {'uid': 'seonjalal-cloudwatch', 'title': 'V1 V2 CloudWatch', 'schemaVersion': 39,
    'version': 1, 'editable': False, 'refresh': '1m', 'time': {'from': 'now-1h', 'to': 'now'},
    'description': 'CloudWatch timestamps preserved; static metric delay is unsupported. Latest Maximum/Minimum per 60s bucket, credits per 300s. Average exports are 15m-window aggregates, not used in these panels. Missing data is not zero. API p95/p99 is measured separately by k6.',
    'panels': panels}
with dashboard_path.open('x') as stream:
    stream.write(json.dumps(dashboard, indent=2) + '\n')
os.chmod(dashboard_path, 0o644)
print('CLOUDWATCH_RDS_AND_EC2_REAL_METRICS_VERIFIED')
print('CLOUDWATCH_SCRAPE_UP=1 HOST_SCRAPE_UP=3')
print('DASHBOARD_URL=http://localhost:13000/d/seonjalal-cloudwatch')
print('BACKUP_DIR=' + str(backup))
print('NO_APP_DB_GRAFANA_CREDENTIAL_OR_IMDS_HOP_CHANGE')
print('LIMITATION=ALB sparse metrics and browser dashboard verification pending; CloudWatch API charges apply')
'''


def build_request():
    remote = REMOTE.replace('__CONFIG__', repr(configuration())).replace('__VERSION__', VERSION)
    compile(remote, '<cloudwatch-install>', 'exec')
    encoded = base64.b64encode(remote.encode()).decode()
    command = "python3 - <<'PY'\nimport base64\nexec(compile(base64.b64decode(" + repr(encoded) + "), '<cloudwatch-install>', 'exec'))\nPY"
    return {'DocumentName': 'AWS-RunShellScript', 'InstanceIds': ['i-0c6a75f630436591c'],
            'Comment': 'Install fixed CloudWatch collector and dashboard on monitoring host only',
            'TimeoutSeconds': 600, 'Parameters': {'commands': [command], 'executionTimeout': ['600']}}


if __name__ == '__main__':
    directory = Path(tempfile.mkdtemp(prefix='seonjalal-cloudwatch-', dir='/private/tmp'))
    os.chmod(directory, 0o700)
    path = directory / 'request.json'
    path.write_text(json.dumps(build_request()))
    os.chmod(path, 0o600)
    print('REQUEST_FILE=' + str(path))
    print('PREPARED_ONLY_NO_AWS_CALL_OR_SERVER_CHANGE')
