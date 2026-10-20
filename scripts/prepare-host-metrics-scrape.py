#!/usr/bin/env python3
"""Mac: 관측 노드의 host scrape/대시보드 연결 요청만 생성. 원격 실행 없음."""
import base64
import json
import os
from pathlib import Path
import tempfile

HOSTS = [
    {'targets': ['172.31.6.212:9100'], 'labels': {'environment': 'v1-benchmark', 'node': 'v1-benchmark'}},
    {'targets': ['10.20.10.236:9100'], 'labels': {'environment': 'v2-staging', 'node': 'v2-ecs-a'}},
    {'targets': ['10.20.11.100:9100'], 'labels': {'environment': 'v2-staging', 'node': 'v2-ecs-c'}},
]


def dashboard():
    queries = [
        ('Host scrape UP (1=up, 0=down)', 'up{job="node_exporter"}', 'short'),
        ('CPU busy %', '100 * (1 - avg by (environment,node) (rate(node_cpu_seconds_total{job="node_exporter",mode="idle"}[1m])))', 'percent'),
        ('Memory used %', '100 * (1 - node_memory_MemAvailable_bytes{job="node_exporter"} / node_memory_MemTotal_bytes{job="node_exporter"})', 'percent'),
        ('Root filesystem used %', '100 * (1 - node_filesystem_avail_bytes{job="node_exporter",mountpoint="/",fstype!="rootfs"} / node_filesystem_size_bytes{job="node_exporter",mountpoint="/",fstype!="rootfs"})', 'percent'),
        ('Load average 1m (not CPU %)', 'node_load1{job="node_exporter"}', 'short'),
        ('CPU steal % (not credit balance)', '100 * avg by (environment,node) (rate(node_cpu_seconds_total{job="node_exporter",mode="steal"}[1m]))', 'percent'),
    ]
    panels = []
    for index, (title, expr, unit) in enumerate(queries):
        panels.append({'id': index + 1, 'title': title, 'type': 'timeseries',
                       'datasource': {'type': 'prometheus', 'uid': 'seonjalal-prometheus'},
                       'gridPos': {'x': 12 * (index % 2), 'y': 8 * (index // 2), 'w': 12, 'h': 8},
                       'targets': [{'refId': 'A', 'expr': expr, 'legendFormat': '{{environment}} / {{node}}'}],
                       'fieldConfig': {'defaults': {'unit': unit}, 'overrides': []},
                       'options': {'legend': {'displayMode': 'list', 'placement': 'bottom'}}})
    return {'uid': 'seonjalal-host-comparison', 'title': 'V1 V2 Host Comparison', 'schemaVersion': 39,
            'version': 1, 'editable': False, 'timezone': 'browser', 'refresh': '15s',
            'time': {'from': 'now-15m', 'to': 'now'}, 'tags': ['seonjalal', 'host-comparison'],
            'description': 'Host metrics only. Container/JVM/RDS/ALB and T3 credit balance are separate. Static targets must be updated if hosts change.',
            'panels': panels}


REMOTE = r'''
import fcntl, json, os, pathlib, subprocess, tempfile, time, urllib.parse, urllib.request

def require(ok, message):
    if not ok:
        raise SystemExit('STOP: ' + message)

def docker(*args):
    result = subprocess.run(['docker', *args], capture_output=True, text=True)
    require(result.returncode == 0, 'Docker 단계 실패: ' + args[0])
    return result.stdout.strip()

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
def fetch(url, as_json=True):
    with opener.open(url, timeout=8) as response:
        body = response.read(8 * 1024**2)
    return json.loads(body) if as_json else body.decode()

require(os.geteuid() == 0, 'root 필요')
request = urllib.request.Request('http://169.254.169.254/latest/api/token', method='PUT',
    headers={'X-aws-ec2-metadata-token-ttl-seconds': '60'})
with opener.open(request, timeout=3) as response:
    token = response.read().decode()
request = urllib.request.Request('http://169.254.169.254/latest/dynamic/instance-identity/document',
    headers={'X-aws-ec2-metadata-token': token})
with opener.open(request, timeout=3) as response:
    identity = json.load(response)
require((identity.get('accountId'), identity.get('region'), identity.get('instanceId')) ==
        ('721744297924', 'ap-northeast-2', 'i-0c6a75f630436591c'), '관측 대상 불일치')
lock_fd = os.open('/run/lock/seonjalal-observability-install.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
try:
    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    raise SystemExit('STOP: 다른 관측 변경 실행 중. 재전송 금지')
root = pathlib.Path('/opt/seonjalal-observability')
require(root.is_dir() and not root.is_symlink(), '기존 관측 경로 필요')
config = root / 'config/prometheus.yml'
require(config.is_file() and not config.is_symlink(), '기존 Prometheus 설정 확인 필요')
old = config.read_bytes()
expected = b"global:\n  scrape_interval: 15s\n  scrape_timeout: 5s\n  external_labels:\n    monitor: staging-observability\nscrape_configs:\n  - job_name: prometheus\n    static_configs:\n      - targets: ['prometheus:9090']\n"
require(old == expected, '설정이 초기값과 다름. 기존 변경 보존 후 검토 필요')
provider = root / 'config/provisioning/dashboards/seonjalal-hosts.yaml'
dashboard_path = root / 'config/provisioning/dashboards/files/seonjalal-hosts.json'
for path in (provider, dashboard_path):
    require(not path.exists() and not path.is_symlink(), '기존 대시보드 파일 보존 필요')
hosts = __HOSTS__
# 실제 대상 연결이 하나라도 안되면 설정 변경 전 중단.
for host in hosts:
    body = fetch('http://' + host['targets'][0] + '/metrics', False)
    require(all(metric in body for metric in ('node_cpu_seconds_total', 'node_memory_MemAvailable_bytes',
                                             'node_filesystem_avail_bytes')), '대상 핵심 metric 응답 확인 필요')
print('HOST_CONNECTIVITY_AND_METRICS_VERIFIED=3', flush=True)
containers = {name: json.loads(docker('inspect', name))[0] for name in
              ('seonjalal-monitor-prometheus', 'seonjalal-monitor-grafana')}
require(all(item['State']['Running'] for item in containers.values()), '기존 관측 컨테이너 상태 확인 필요')
grafana_mounts = containers['seonjalal-monitor-grafana']['Mounts']
require(any(m['Destination'] == '/etc/grafana/provisioning' and
            m['Source'] == str(root / 'config/provisioning') for m in grafana_mounts), 'provisioning mount 불일치')
prom_config = {'global': {'scrape_interval': '15s', 'scrape_timeout': '5s',
                         'external_labels': {'monitor': 'staging-observability'}},
               'scrape_configs': [{'job_name': 'prometheus', 'static_configs': [{'targets': ['prometheus:9090']}]},
                                  {'job_name': 'node_exporter', 'sample_limit': 5000, 'static_configs': hosts}]}
new = (json.dumps(prom_config, indent=2) + '\n').encode()
os.umask(0o077)
backup = pathlib.Path(tempfile.mkdtemp(prefix='before-host-scrape-', dir=root))
(backup / 'prometheus.yml').write_bytes(old)
candidate = backup / 'candidate.yml'
candidate.write_bytes(new)
os.chmod(candidate, 0o644)
docker('run', '--rm', '--network', 'none', '--memory', '256m', '--memory-swap', '256m',
       '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
       '-v', str(candidate) + ':/candidate.yml:ro', '--entrypoint', '/bin/promtool',
       containers['seonjalal-monitor-prometheus']['Image'], 'check', 'config', '/candidate.yml')

def write_same_inode(payload):
    # 단일 파일 bind mount이므로 inode 교체 없이 내용을 갱신해야 컨테이너에 반영됨.
    with config.open('r+b') as stream:
        stream.write(payload)
        stream.truncate()
        stream.flush()
        os.fsync(stream.fileno())

try:
    write_same_inode(new)
    docker('kill', '--signal=HUP', 'seonjalal-monitor-prometheus')
    up = False
    for attempt in range(35):
        targets = fetch('http://127.0.0.1:9090/api/v1/targets')['data']['activeTargets']
        nodes = [item for item in targets if item.get('labels', {}).get('job') == 'node_exporter']
        if len(nodes) == 3 and all(item.get('health') == 'up' for item in nodes):
            up = True
            break
        time.sleep(2)
    require(up, '3대 scrape UP 미확인')
except BaseException:
    write_same_inode(old)
    docker('kill', '--signal=HUP', 'seonjalal-monitor-prometheus')
    print('PROMETHEUS_PREVIOUS_CONFIG_RESTORED')
    raise

# 기존 datasource/계정/비밀번호/DB/볼륨을 수정하지 않고 새 dashboard provider만 추가.
for path in (provider.parent, dashboard_path.parent):
    require(not path.is_symlink(), '대시보드 경로 확인 필요')
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, 0o755)
provider_config = {'apiVersion': 1, 'providers': [{'name': 'seonjalal-hosts', 'orgId': 1,
    'folder': 'Seonjalal', 'type': 'file', 'disableDeletion': True, 'allowUiUpdates': False,
    'updateIntervalSeconds': 15, 'options': {'path': '/etc/grafana/provisioning/dashboards/files'}}]}
with dashboard_path.open('x') as stream:
    stream.write(json.dumps(__DASHBOARD__, indent=2) + '\n')
os.chmod(dashboard_path, 0o644)
with provider.open('x') as stream:
    stream.write(json.dumps(provider_config, indent=2) + '\n')
os.chmod(provider, 0o644)
# 신규 provider를 읽도록 Grafana만 재시작. 기존 영속 볼륨과 변경한 비밀번호 보존.
docker('restart', '--time', '30', 'seonjalal-monitor-grafana')
healthy = False
for attempt in range(30):
    try:
        healthy = fetch('http://127.0.0.1:3000/api/health').get('database') == 'ok'
        if healthy:
            break
    except (OSError, ValueError):
        pass
    time.sleep(2)
require(healthy, 'Grafana 재시작 후 health 확인 필요')
print('HOST_SCRAPE_UP=3')
print('GRAFANA_HEALTH_OK')
print('DASHBOARD_URL=http://localhost:13000/d/seonjalal-host-comparison')
print('BACKUP_DIR=' + str(backup))
print('NO_APP_DB_SECURITY_GROUP_OR_GRAFANA_CREDENTIAL_CHANGE')
print('LIMITATION=Dashboard browser verification and container/RDS/ALB metrics pending; static host targets')
'''


def build_request():
    remote = REMOTE.replace('__HOSTS__', repr(HOSTS)).replace('__DASHBOARD__', repr(dashboard()))
    compile(remote, '<host-scrape>', 'exec')
    encoded = base64.b64encode(remote.encode()).decode()
    command = "python3 - <<'PY'\nimport base64\nexec(compile(base64.b64decode(" + repr(encoded) + "), '<host-scrape>', 'exec'))\nPY"
    return {'DocumentName': 'AWS-RunShellScript', 'InstanceIds': ['i-0c6a75f630436591c'],
            'Comment': 'Connect three nonproduction host exporters and provision host dashboard',
            'TimeoutSeconds': 300, 'Parameters': {'commands': [command], 'executionTimeout': ['300']}}


if __name__ == '__main__':
    directory = Path(tempfile.mkdtemp(prefix='seonjalal-host-scrape-', dir='/private/tmp'))
    os.chmod(directory, 0o700)
    path = directory / 'request.json'
    path.write_text(json.dumps(build_request()))
    os.chmod(path, 0o600)
    print('REQUEST_FILE=' + str(path))
    print('PREPARED_ONLY_NO_AWS_CALL_OR_SERVER_CHANGE')
