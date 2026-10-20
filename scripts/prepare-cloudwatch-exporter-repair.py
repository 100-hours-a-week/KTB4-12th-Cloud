#!/usr/bin/env python3
"""Mac: 중지된 기존 CloudWatch 수집기의 설정 수정·연결 요청만 생성."""
import base64
import copy
import importlib.util
import json
import os
from pathlib import Path
import tempfile

spec = importlib.util.spec_from_file_location('cw_install', Path(__file__).with_name('prepare-cloudwatch-exporter.py'))
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def original_configuration():
    config = copy.deepcopy(installer.configuration())
    for job in config['static']:
        for metric in job['metrics']:
            metric['statistics'] = metric['statistics'][:1]
            metric['delay'] = 120
    alb = config['static'][1]['metrics']
    percentile = copy.deepcopy(next(m for m in alb if m['name'] == 'TargetResponseTime'))
    percentile['statistics'] = ['p95']
    alb.insert(2, percentile)
    return config


PRELUDE = r'''
import fcntl, json, math, os, pathlib, re, subprocess, tempfile, time, urllib.request

def require(ok, message):
    if not ok:
        raise SystemExit('STOP: ' + message)

def run(*args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=90)
    require(result.returncode == 0, '명령 실패: ' + args[0] + '; 재전송 금지')
    return result.stdout.strip()

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
def fetch(url):
    with opener.open(url, timeout=8) as response:
        return response.read(8 * 1024**2)

def api(url):
    return json.loads(fetch(url))

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
    raise SystemExit('STOP: 다른 관측 변경 실행 중')
root = pathlib.Path('/opt/seonjalal-cloudwatch-exporter')
monitor = pathlib.Path('/opt/seonjalal-observability')
config_path = root / 'config.yml'
executable = root / 'yace'
for path in (root, monitor, config_path, executable):
    require(path.exists() and not path.is_symlink(), '기존 설치 경로 검증 필요')
require('0.68.0' in run(str(executable), 'version'), '기존 YACE 버전 확인 필요')
status = subprocess.run(['systemctl', 'is-active', 'seonjalal-cloudwatch-exporter.service'],
                        capture_output=True, text=True)
require(status.stdout.strip() in ('inactive', 'failed'), '기존 수집기는 중지 상태여야 함')
old_cw = config_path.read_bytes()
require(json.loads(old_cw) == __OLD_CONFIG__, '설치 후 설정 변경 감지: 원본 보존 후 검토 필요')
prom_path = monitor / 'config/prometheus.yml'
require(prom_path.is_file() and not prom_path.is_symlink(), 'Prometheus 설정 확인 필요')
old = prom_path.read_bytes()
prom_config = json.loads(old)
require({job['job_name'] for job in prom_config['scrape_configs']} == {'prometheus', 'node_exporter'},
        '이전 host 수집 설정과 불일치')
dashboard_path = monitor / 'config/provisioning/dashboards/files/seonjalal-cloudwatch.json'
require(dashboard_path.parent.is_dir() and not dashboard_path.exists() and not dashboard_path.is_symlink(),
        '기존 dashboard 보존 필요')
prom = json.loads(run('docker', 'inspect', 'seonjalal-monitor-prometheus'))[0]
require(prom['State']['Running'], '기존 Prometheus 활성 필요')
network = json.loads(run('docker', 'network', 'inspect', 'seonjalal-monitor'))[0]
gateway = network['IPAM']['Config'][0]['Gateway']
require(network['Driver'] == 'bridge', '전용 Docker bridge 확인 필요')
require('--listen-address=' + gateway + ':5000' in pathlib.Path(
    '/etc/systemd/system/seonjalal-cloudwatch-exporter.service').read_text(), '기존 listen 설정 확인 필요')
os.umask(0o077)
backup = pathlib.Path(tempfile.mkdtemp(prefix='before-cloudwatch-repair-', dir=monitor))
(backup / 'prometheus.yml').write_bytes(old)
(backup / 'cloudwatch-config.yml').write_bytes(old_cw)
candidate = backup / 'cloudwatch-candidate.yml'
candidate.write_text(json.dumps(__NEW_CONFIG__, indent=2) + '\n')
run(str(executable), 'verify-config', '--config.file=' + str(candidate))
# 다운로드/바이너리/서비스계정/IAM을 바꾸지 않고 기존 수집 설정만 갱신.
config_path.write_bytes(candidate.read_bytes())
os.chmod(config_path, 0o644)
try:
    run('systemctl', 'reset-failed', 'seonjalal-cloudwatch-exporter.service')
    run('systemctl', 'start', 'seonjalal-cloudwatch-exporter.service')
except BaseException:
    config_path.write_bytes(old_cw)
    os.chmod(config_path, 0o644)
    raise
print('CLOUDWATCH_STATIC_PERCENTILE_AND_DELAY_CONFIG_CORRECTED', flush=True)
'''


def build_remote():
    # 검증·연결·실패 시 새 collector 중지 동작은 최초 설치와 같은 구현 재사용.
    marker = 'def same_inode(data):'
    assert installer.REMOTE.count(marker) == 1
    tail = marker + installer.REMOTE.split(marker, 1)[1]
    remote = PRELUDE.replace('__OLD_CONFIG__', repr(original_configuration())).replace(
        '__NEW_CONFIG__', repr(installer.configuration())) + '\n' + tail
    compile(remote, '<cloudwatch-repair>', 'exec')
    return remote


def build_request():
    encoded = base64.b64encode(build_remote().encode()).decode()
    command = "python3 - <<'PY'\nimport base64\nexec(compile(base64.b64decode(" + repr(encoded) + "), '<cloudwatch-repair>', 'exec'))\nPY"
    return {'DocumentName': 'AWS-RunShellScript', 'InstanceIds': ['i-0c6a75f630436591c'],
            'Comment': 'Correct stopped YACE static percentile panic and connect metrics; no reinstall',
            'TimeoutSeconds': 600, 'Parameters': {'commands': [command], 'executionTimeout': ['600']}}


if __name__ == '__main__':
    directory = Path(tempfile.mkdtemp(prefix='seonjalal-cloudwatch-repair-', dir='/private/tmp'))
    os.chmod(directory, 0o700)
    path = directory / 'request.json'
    path.write_text(json.dumps(build_request()))
    os.chmod(path, 0o600)
    print('REQUEST_FILE=' + str(path))
    print('PREPARED_ONLY_NO_AWS_CALL_OR_SERVER_CHANGE')
