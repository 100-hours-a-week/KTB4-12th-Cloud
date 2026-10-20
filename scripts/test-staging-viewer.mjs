// Node 내 합성 이벤트만 실행. AWS·원격 서버 변경 없음.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const template = readFileSync(new URL('../infrastructure/terraform/environments/shared/frontend/staging-viewer.js.tftpl', import.meta.url), 'utf8');
const context = vm.createContext({});
vm.runInContext(template.replace('${allowed_ipv4_json}', JSON.stringify('203.0.113.10')), context);
const event = (ip, uri = '/', method = 'GET', headers = {}) => ({viewer: {ip}, request: {uri, method, headers}});
let passed = 0;
function check(name, run) { run(); passed++; console.log('PASS ' + name); }
check('비허용 IP 거절', () => assert.equal(context.handler(event('198.51.100.2')).statusCode, 403));
check('전달 헤더 위조로 우회 불가', () => assert.equal(context.handler(event('198.51.100.2', '/', 'GET', {'x-forwarded-for': {value: '203.0.113.10'}})).statusCode, 403));
check('Viewer 없음 거절', () => assert.equal(context.handler({request: {uri: '/', method: 'GET'}}).statusCode, 403));
check('루트 SPA 반환', () => assert.equal(context.handler(event('203.0.113.10')).uri, '/index.html'));
check('화면 경로 SPA 반환', () => assert.equal(context.handler(event('203.0.113.10', '/products/4')).uri, '/index.html'));
check('Asset 경로 보존', () => assert.equal(context.handler(event('203.0.113.10', '/assets/index.js')).uri, '/assets/index.js'));
check('HEAD 허용', () => assert.equal(context.handler(event('203.0.113.10', '/index.html', 'HEAD')).method, 'HEAD'));
check('POST 차단', () => assert.equal(context.handler(event('203.0.113.10', '/', 'POST')).statusCode, 403));
for (const uri of ['/api', '/api/products', '/frontend/production/index.html', '/assets/../secret', '/assets/%2e%2e/secret', '/assets/\\secret', '/missing.js']) {
    check('경로 거절 ' + uri, () => assert.equal(context.handler(event('203.0.113.10', uri)).statusCode, 403));
}
console.log('VIEWER_SYNTHETIC_TESTS_PASSED=' + passed);
