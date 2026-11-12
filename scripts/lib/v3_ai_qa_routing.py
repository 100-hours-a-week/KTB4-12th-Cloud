"""#131의 추가 경로만 준비/적용/복구. 기존 CF 설정은 메모리에서 보존하며 출력·저장하지 않음."""
import argparse
import copy
import datetime
import hashlib
import json
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = Path('/private/tmp/seonjalal-v3-qa')
KUBE = ['kubectl', '--context', 'seonjalal-v3', '--namespace', 'production']
AWS = ['aws', '--profile', 'seonjalal-production', '--region', 'ap-northeast-2']
SERVICE = 'ai-chat-admin'
DISTRIBUTION = 'E15EPL07TNUJQL'
CACHE_DISABLED = '4135ea2d-6df8-44a3-9df3-4b5a84be39ad'
ORIGIN_POLICY = 'b689b0a8-53d0-40ab-baf2-68738e2966ac'
PATH = {'path': '/admin', 'pathType': 'Prefix',
        'backend': {'service': {'name': SERVICE, 'port': {'number': 7860}}}}


def require(value, reason):
    if not value:
        raise RuntimeError(reason)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def call(command, payload=None):
    try:
        result = subprocess.run(command, input=json.dumps(payload) if payload is not None else None,
                                capture_output=True, text=True, timeout=50)
    except (OSError, subprocess.TimeoutExpired):
        raise RuntimeError('command_timeout_or_unavailable — 재실행 전 실제 상태 조회 필요') from None
    require(result.returncode == 0, 'command_failed — 오류 원문 미출력; 실제 상태 조회 필요')
    return json.loads(result.stdout) if result.stdout.strip() else {}


def kube(*args):
    return call(KUBE + list(args) + ['--output', 'json', '--request-timeout=30s'])


def aws(*args, payload=None):
    return call(AWS + list(args) + ['--output', 'json', '--no-cli-pager',
                '--cli-connect-timeout', '5', '--cli-read-timeout', '20'], payload)


def save(name, value):
    ARTIFACTS.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = ARTIFACTS / name
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')
    path.chmod(0o600)
    return path


def behavior(config):
    # 관리자 전용: API 원본·전달 계약은 재사용하고 URI 변환 함수는 연결하지 않음.
    original = next((b for b in config.get('CacheBehaviors', {}).get('Items', []) if b['PathPattern'] == '/api/*'), None)
    require(original is not None and original['TargetOriginId'] == 'seonjalal-v3-backend', 'unexpected_api_origin')
    require(original.get('CachePolicyId') == CACHE_DISABLED and original.get('OriginRequestPolicyId') == ORIGIN_POLICY,
            'unexpected_api_cache_or_forwarding_policy')
    require(original.get('LambdaFunctionAssociations', {}).get('Quantity', 0) == 0, 'unreviewed_lambda_association')
    result = copy.deepcopy(original)
    result['PathPattern'] = '/admin/*'
    result['ViewerProtocolPolicy'] = 'redirect-to-https'
    result['FunctionAssociations'] = {'Quantity': 0}
    result['LambdaFunctionAssociations'] = {'Quantity': 0}
    result['AllowedMethods'] = {'Quantity': 7, 'Items': ['GET', 'HEAD', 'OPTIONS', 'PUT', 'PATCH', 'POST', 'DELETE'],
                                'CachedMethods': {'Quantity': 2, 'Items': ['GET', 'HEAD']}}
    return result


def add_behavior(config):
    candidate = copy.deepcopy(config)
    items = candidate.setdefault('CacheBehaviors', {}).setdefault('Items', [])
    require(not any(b['PathPattern'].startswith('/admin') for b in items), 'admin_behavior_already_exists')
    # 기존 동작 순서를 유지하도록 마지막에 추가. /api/*와 겹치지 않음.
    items.append(behavior(config))
    candidate['CacheBehaviors']['Quantity'] = len(items)
    return candidate


def ingress_patch(ingress):
    rules = ingress['spec'].get('rules', [])
    candidates = []
    for index, rule in enumerate(rules):
        paths = rule.get('http', {}).get('paths', [])
        require(not any(p.get('path', '').startswith('/admin') for p in paths), 'admin_ingress_route_already_exists')
        if any(p.get('path') == '/recommendations' and p.get('backend', {}).get('service', {}).get('name') == 'recommendation-service' for p in paths):
            require(rule.get('host') in (None, 'www.seonjalal.com', 'v3-origin.seonjalal.com'), 'unreviewed_ingress_host')
            candidates.append((index, paths))
    require(len(candidates) == 1, 'ambiguous_ingress_rule')
    index, paths = candidates[0]
    pointer = '/spec/rules/{}/http/paths'.format(index)
    return [{'op': 'test', 'path': '/metadata/resourceVersion', 'value': ingress['metadata']['resourceVersion']},
            {'op': 'test', 'path': pointer, 'value': paths},
            {'op': 'add', 'path': pointer + '/-', 'value': PATH}]


def current_pods():
    pods = kube('get', 'pods', '--selector', 'app.kubernetes.io/instance=ai-runtime')['items']
    return sorted(p['metadata']['uid'] for p in pods if not p['metadata'].get('deletionTimestamp'))


def verify_image_capabilities():
    # 사전 조회 결과와 현재 Pod·이미지 선언을 대조해 검토 후 변경을 탐지.
    proof = json.loads(Path('/private/tmp/seonjalal-v3-monitoring/measurements/qa-routing-preflight.json').read_text())
    caps = next(row['deployedAdminCode'] for row in proof if 'deployedAdminCode' in row)
    require(all(caps.get(k) is True for k in ('adminPageRoutePresent', 'adminAssetRoutePresent',
                'adminPageUsesScopedAssets', 'adminPublicOriginValidationPresent', 'loginUsesAdminOrigin')), 'image_not_admin_ready')
    require(caps.get('environment') == 'production' and caps.get('cookieSecure') == 'true', 'admin_security_settings_not_ready')
    expected = next(row['images'] for row in proof if row.get('deployment') == 'ai-runtime')
    dep = kube('get', 'deployment', 'ai-runtime')
    require({c['name']: c['image'] for c in dep['spec']['template']['spec']['containers']} == expected, 'ai_image_changed_after_review')
    old_pod = next(row['pod'] for row in proof if 'deployedAdminCode' in row)
    pods = kube('get', 'pods', '--selector', 'app.kubernetes.io/instance=ai-runtime')['items']
    require(any(p['metadata']['name'] == old_pod and not p['metadata'].get('deletionTimestamp') and
                len(p.get('status', {}).get('containerStatuses', [])) == len(p['spec']['containers']) and
                all(c['ready'] for c in p['status']['containerStatuses']) for p in pods), 'reviewed_ai_pod_not_ready')


def snapshot():
    require(aws('sts', 'get-caller-identity')['Account'] == '721744297924', 'account_mismatch')
    verify_image_capabilities()
    distribution = aws('cloudfront', 'get-distribution', '--id', DISTRIBUTION)
    require(distribution['Distribution']['Status'] == 'Deployed', 'cloudfront_update_already_in_progress')
    response = aws('cloudfront', 'get-distribution-config', '--id', DISTRIBUTION)
    ingress = kube('get', 'ingress', 'production-entry')
    services = kube('get', 'services')['items']
    require(not any(s['metadata']['name'] == SERVICE for s in services), 'admin_service_already_exists')
    config = response['DistributionConfig']
    candidate = add_behavior(config)
    patch = ingress_patch(ingress)
    manifest = json.loads((ROOT / 'operations/ai-qa/service.json').read_text())
    require(manifest['spec']['selector'] == kube('get', 'service', 'ai-chat')['spec']['selector'], 'ai_service_selector_mismatch')
    summary = {'atUTC': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'distribution': DISTRIBUTION,
               'etag': response['ETag'], 'configSha256': digest(config), 'candidateSha256': digest(candidate),
               'ingressResourceVersion': ingress['metadata']['resourceVersion'], 'ingressSpecSha256': digest(ingress['spec']),
               'serviceSha256': digest(manifest), 'aiPodUIDs': current_pods(),
               'newBehavior': behavior(config), 'newIngressPath': PATH,
               'preservedApiBehaviors': [b['PathPattern'] for b in config['CacheBehaviors']['Items']],
               'podRestartRequired': False, 'terraformUsed': False}
    return summary, config, candidate, patch


def plan():
    summary, _, _, patch = snapshot()
    kube('apply', '--filename', str(ROOT / 'operations/ai-qa/service.json'), '--dry-run=server')
    patch_path = save('ingress-add.json', patch)
    kube('patch', 'ingress', 'production-entry', '--type=json', '--patch-file', str(patch_path), '--dry-run=server')
    save('plan.json', summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print('QA_ROUTING_PLAN_READY — server dry run 완료; 실제 설정 변경 없음')


def change_cloudfront(config, etag):
    # 전체 원본 설정에는 기존 custom header가 있을 수 있어 파일·argv·출력에 쓰지 않음.
    return aws('cloudfront', 'update-distribution', '--id', DISTRIBUTION, '--if-match', etag,
               '--distribution-config', 'file:///dev/stdin', payload=config)


def wait_target_health():
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        bindings = kube('get', 'targetgroupbindings')['items']
        targets = [b['spec']['targetGroupARN'] for b in bindings if b['spec'].get('serviceRef', {}).get('name') == SERVICE]
        if len(targets) == 1:
            rows = aws('elbv2', 'describe-target-health', '--target-group-arn', targets[0])['TargetHealthDescriptions']
            healthy = sum(r['TargetHealth']['State'] == 'healthy' for r in rows)
            if healthy:
                return {'registered': len(rows), 'healthy': healthy}
        print('관리자 대상 그룹 준비 확인 중', flush=True)
        time.sleep(5)
    raise RuntimeError('admin_target_not_healthy — CloudFront 변경 중단')


def wait_distribution():
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        if aws('cloudfront', 'get-distribution', '--id', DISTRIBUTION)['Distribution']['Status'] == 'Deployed':
            return
        print('CloudFront 관리자 경로 전파 확인 중', flush=True)
        time.sleep(15)
    raise RuntimeError('cloudfront_propagation_wait_timeout — 상태 재조회 필요')


def public_checks():
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    cases = [('/admin/qa', {}, 200), ('/admin/static/admin.js', {}, 200),
             ('/admin/static/admin.css', {}, 200), ('/admin/static/http.js', {}, 200),
             ('/admin/api/conversations', {}, 401),
             ('/admin/api/conversations', {'Origin': 'https://untrusted.invalid'}, 403),
             ('/api/health', {}, 200), ('/api/products', {}, 200), ('/', {}, 200)]
    checks = []
    for path, headers, expected in cases:
        try:
            with opener.open(urllib.request.Request('https://www.seonjalal.com' + path, headers=headers), timeout=20) as response:
                code, kind = response.status, response.headers.get('Content-Type', '')
                # 미인증 API가 잘못 공개된 경우에도 관리자 데이터는 읽지 않음.
        except urllib.error.HTTPError as error:
            code, kind = error.code, error.headers.get('Content-Type', '')
        ok = code == expected
        if path.startswith('/admin/static/'):
            ok = ok and ('javascript' in kind if path.endswith('.js') else 'text/css' in kind)
        if path.startswith('/admin/api/'):
            ok = ok and 'json' in kind
        row = {'path': path, 'untrustedOrigin': bool(headers), 'HTTP': code, 'expected': expected, 'contentType': kind, 'passed': ok}
        checks.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    require(all(c['passed'] for c in checks), 'public_checks_failed — 관리자 경로 복구 필요 여부 확인')
    return checks


def _apply(operation_id):
    previous = json.loads((ARTIFACTS / 'plan.json').read_text())
    summary, config, candidate, patch = snapshot()
    for key in ('etag', 'configSha256', 'candidateSha256', 'ingressResourceVersion', 'ingressSpecSha256', 'serviceSha256', 'aiPodUIDs'):
        require(summary[key] == previous[key], 'plan_drift:' + key)
    receipt = {'operationId': operation_id, 'startedAtUTC': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'events': [], 'finished': False}
    def mark(phase, **fields):
        event = {'phase': phase, 'atUTC': datetime.datetime.now(datetime.timezone.utc).isoformat(), **fields}
        receipt['events'].append(event)
        save('apply-receipt.json', receipt)
        print(json.dumps(event, ensure_ascii=False), flush=True)
    kube('apply', '--filename', str(ROOT / 'operations/ai-qa/service.json'))
    service = kube('get', 'service', SERVICE)
    mark('service_created', nodePort=service['spec']['ports'][0]['nodePort'])
    patch_file = save('ingress-add.json', patch)
    kube('patch', 'ingress', 'production-entry', '--type=json', '--patch-file', str(patch_file))
    mark('admin_ingress_added')
    health = wait_target_health()
    mark('admin_targets_healthy', **health)
    fresh = aws('cloudfront', 'get-distribution-config', '--id', DISTRIBUTION)
    require(fresh['ETag'] == previous['etag'] and digest(fresh['DistributionConfig']) == previous['configSha256'], 'cloudfront_changed_during_target_wait')
    change_cloudfront(candidate, previous['etag'])
    mark('admin_cloudfront_submitted')
    wait_distribution()
    mark('admin_cloudfront_deployed')
    receipt['checks'] = public_checks()
    receipt['sameAiPodUIDs'] = current_pods() == previous['aiPodUIDs']
    receipt['finished'] = True
    receipt['authenticatedLoginVerified'] = False
    save('apply-receipt.json', receipt)
    print('QA_ROUTING_APPLIED — 공개 경로 검사 통과; Pod UID 보존 여부는 receipt 확인; 로그인은 사용자 화면에서 최종 확인 필요')


def rollback():
    # 이 도구가 추가한 동작·경로만 제거. Service는 삭제하지 않고 조사용으로 보존.
    response = aws('cloudfront', 'get-distribution-config', '--id', DISTRIBUTION)
    config = response['DistributionConfig']
    items = config.get('CacheBehaviors', {}).get('Items', [])
    matches = [b for b in items if b['PathPattern'] == '/admin/*']
    if matches:
        require(len(matches) == 1 and matches[0] == behavior(config), 'admin_behavior_changed — 복구 중단')
        config['CacheBehaviors']['Items'] = [b for b in items if b['PathPattern'] != '/admin/*']
        config['CacheBehaviors']['Quantity'] = len(config['CacheBehaviors']['Items'])
        change_cloudfront(config, response['ETag'])
        wait_distribution()
    ingress = kube('get', 'ingress', 'production-entry')
    removals = []
    for i, rule in enumerate(ingress['spec'].get('rules', [])):
        for j, path in enumerate(rule.get('http', {}).get('paths', [])):
            if path.get('path', '').startswith('/admin'):
                require(path == PATH, 'admin_ingress_changed — 복구 중단')
                removals.append('/spec/rules/{}/http/paths/{}'.format(i, j))
    require(len(removals) <= 1, 'ambiguous_admin_routes')
    if removals:
        patch = [{'op': 'test', 'path': '/metadata/resourceVersion', 'value': ingress['metadata']['resourceVersion']},
                 {'op': 'test', 'path': removals[0], 'value': PATH}, {'op': 'remove', 'path': removals[0]}]
        path = save('ingress-remove.json', patch)
        kube('patch', 'ingress', 'production-entry', '--type=json', '--patch-file', str(path))
    print('QA_ROUTING_ROLLED_BACK — 관리자 전달 경로만 제거; Service/Pod/기존 API 보존')


def apply():
    operation_id = str(uuid.uuid4())
    try:
        _apply(operation_id)
    except Exception:
        # 이번 실행의 변경 영수증이 있는 경우만 자동 복구. 과거 실패 기록으로 다른 작업을 건드리지 않음.
        receipt_path = ARTIFACTS / 'apply-receipt.json'
        receipt = json.loads(receipt_path.read_text()) if receipt_path.exists() else {}
        if receipt.get('operationId') == operation_id and receipt.get('events'):
            print('QA 검사 실패: 이번 실행에서 추가한 관리자 전달 경로 복구 시도', flush=True)
            try:
                rollback()
                receipt['rollbackCompleted'] = True
            except Exception:
                receipt['rollbackCompleted'] = False
                print('자동 복구 미완료 — 실제 CloudFront/Ingress 상태 확인 필요', flush=True)
            save('apply-receipt.json', receipt)
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=('plan', 'apply', 'rollback'), default='plan')
    args = parser.parse_args()
    try:
        {'plan': plan, 'apply': apply, 'rollback': rollback}[args.mode]()
    except Exception as error:
        print(str(error) if type(error) is RuntimeError else 'STOP: operation_failed — 원문 미출력')
        print('부분 적용 가능성: /private/tmp/seonjalal-v3-qa/apply-receipt.json 및 실제 상태 확인 후 계속; 무조건 재실행 금지')
        raise SystemExit(1)
