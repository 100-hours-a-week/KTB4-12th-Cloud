#!/usr/bin/env python3
"""기존 수집 설정을 보존하며 Develop host 대상만 추가하는 요청 생성."""
import base64
import importlib.util
import json
import os
from pathlib import Path
import tempfile

spec = importlib.util.spec_from_file_location(
    "host_scrape", Path(__file__).with_name("prepare-host-metrics-scrape.py"))
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)

TRANSFORM = r'''
def add_develop(config):
    import copy
    result = copy.deepcopy(config)
    jobs = result['scrape_configs']
    names = [job['job_name'] for job in jobs]
    if len(names) != len(set(names)) or set(names) != {'prometheus', 'node_exporter', 'cloudwatch'}:
        raise ValueError('기존 수집 job 구성 불일치')
    node = next(job for job in jobs if job['job_name'] == 'node_exporter')
    expected = __HOSTS__
    if node.get('static_configs') != expected:
        raise ValueError('기존 host 대상 변경 또는 Develop 중복 추가 여부 확인 필요')
    node['static_configs'].append({
        'targets': ['10.20.0.28:9100'],
        'labels': {'environment': 'v2-develop', 'node': 'v2-develop-runtime'}})
    return result
'''.replace('__HOSTS__', repr(base.HOSTS))

REMOTE = base.REMOTE.split('\nexpected = b', 1)[0] + TRANSFORM + r'''
require(len(old) < 1024 * 1024, '설정 크기 확인 필요')
try:
    current = json.loads(old)
    updated = add_develop(current)
except (ValueError, KeyError, TypeError):
    raise SystemExit('STOP: 기존 JSON 수집 설정/host 대상 불일치. 설정 보존')

# 실제 exporter 연결과 기존 모든 target UP을 먼저 확인.
body = fetch('http://10.20.0.28:9100/metrics', False)
require(all(x in body for x in ('node_cpu_seconds_total', 'node_memory_MemAvailable_bytes',
                               'node_filesystem_avail_bytes')), 'Develop 핵심 metric 확인 필요')
old_targets = fetch('http://127.0.0.1:9090/api/v1/targets')['data']['activeTargets']
require(len(old_targets) == 5 and all(t.get('health') == 'up' for t in old_targets),
        '기존5개 target 상태 확인 필요. 변경 전 중단')
container = json.loads(docker('inspect', 'seonjalal-monitor-prometheus'))[0]
require(container['State']['Running'], '기존 Prometheus 실행 상태 확인 필요')
before_inode = config.stat().st_ino
new = (json.dumps(updated, indent=2) + '\n').encode()
os.umask(0o077)
backup = pathlib.Path(tempfile.mkdtemp(prefix='before-develop-scrape-', dir=root))
(backup / 'prometheus.yml').write_bytes(old)
candidate = backup / 'candidate.yml'
candidate.write_bytes(new)
os.chmod(candidate, 0o644)
docker('run', '--rm', '--network', 'none', '--memory', '256m', '--memory-swap', '256m',
       '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges:true',
       '-v', str(candidate) + ':/candidate.yml:ro', '--entrypoint', '/bin/promtool',
       container['Image'], 'check', 'config', '/candidate.yml')

def write_same_inode(payload):
    require(config.stat().st_ino == before_inode, '설정 inode 변경. 동시 변경 여부 확인 필요')
    with config.open('r+b') as stream:
        stream.write(payload)
        stream.truncate()
        stream.flush()
        os.fsync(stream.fileno())

require(config.read_bytes() == old, '동시 설정 변경 발견. 적용 전 중단')
try:
    write_same_inode(new)
    docker('kill', '--signal=HUP', 'seonjalal-monitor-prometheus')
    ready = False
    for attempt in range(35):
        try:
            targets = fetch('http://127.0.0.1:9090/api/v1/targets')['data']['activeTargets']
            nodes = [t for t in targets if t.get('labels', {}).get('job') == 'node_exporter']
            cloudwatch = [t for t in targets if t.get('labels', {}).get('job') == 'cloudwatch']
            develop = [t for t in nodes if t.get('labels', {}).get('environment') == 'v2-develop'
                       and t.get('labels', {}).get('instance') == '10.20.0.28:9100']
            ready = (len(targets) == 6 and len(nodes) == 4 and len(cloudwatch) == 1
                     and len(develop) == 1 and all(t.get('health') == 'up' for t in targets))
            if ready:
                break
        except (OSError, ValueError, KeyError):
            pass
        time.sleep(2)
    require(ready, '추가 후 host4개/CloudWatch 및 자체 수집 UP 확인 필요')
except BaseException:
    write_same_inode(old)
    docker('kill', '--signal=HUP', 'seonjalal-monitor-prometheus')
    print('PREVIOUS_PROMETHEUS_CONFIG_RESTORED_VERIFY_TARGETS')
    raise

print('DEVELOP_HOST_SCRAPE_UP=1 HOST_SCRAPE_UP=4 CLOUDWATCH_SCRAPE_UP=1')
print('BACKUP_DIR=' + str(backup))
print('DASHBOARD_URL=http://localhost:13000/d/seonjalal-host-comparison')
print('NO_APP_DB_GRAFANA_OR_EXPORTER_RESTART_NO_CREDENTIAL_CHANGE')
print('LIMITATION=Host metrics only; container/JVM/pool metrics and browser verification pending')
'''


def build_request():
    compile(REMOTE, '<develop-host-scrape>', 'exec')
    encoded = base64.b64encode(REMOTE.encode()).decode()
    command = "python3 - <<'PY'\nimport base64\nexec(compile(base64.b64decode(" + repr(encoded) + "), '<develop-host-scrape>', 'exec'))\nPY"
    return {'DocumentName': 'AWS-RunShellScript', 'InstanceIds': ['i-0c6a75f630436591c'],
            'Comment': 'Append Develop host target; preserve existing host and CloudWatch scraping',
            'TimeoutSeconds': 300, 'MaxConcurrency': '1', 'MaxErrors': '0',
            'Parameters': {'commands': [command], 'executionTimeout': ['300']}}


if __name__ == '__main__':
    directory = Path(tempfile.mkdtemp(prefix='seonjalal-develop-host-scrape-', dir='/private/tmp'))
    os.chmod(directory, 0o700)
    path = directory / 'request.json'
    path.write_text(json.dumps(build_request()))
    os.chmod(path, 0o600)
    print('REQUEST_FILE=' + str(path))
    print('PREPARED_ONLY_NO_AWS_CALL_OR_SERVER_CHANGE')
