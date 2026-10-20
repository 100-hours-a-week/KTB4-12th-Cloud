// 외부 호출 없이 주요 URI 변환과 인증/쿼리 보존 검사.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function load(file) {
  const source = fs.readFileSync(path.join(__dirname, '..', file), 'utf8');
  return vm.runInNewContext(source + '\nhandler;');
}
const api = load('api-path.js');
const spa = load('spa-path.js');

test('상태 API는 기존 Actuator 경로로 전달', () => {
  assert.equal(api({ request: { uri: '/api/health' } }).uri, '/actuator/health');
});
test('일반 API는 접두어만 제거하고 인증·쿼리·본문·메서드 보존', () => {
  const request = {
    uri: '/api/products', method: 'POST',
    querystring: { sort: { value: 'POPULAR' } },
    headers: { authorization: { value: 'TEST_ONLY' } },
    body: 'TEST_ONLY'
  };
  assert.equal(api({ request }), request);
  assert.equal(request.uri, '/products');
  assert.equal(request.headers.authorization.value, 'TEST_ONLY');
  assert.equal(request.querystring.sort.value, 'POPULAR');
  assert.equal(request.body, 'TEST_ONLY');
  assert.equal(request.method, 'POST');
});
test('상태 API의 하위 경로를 상태 API로 오인하지 않음', () => {
  assert.equal(api({ request: { uri: '/api/health/extra' } }).uri, '/health/extra');
});
test('SPA 루트와 하위 화면 요청은 index로 전달', () => {
  for (const uri of ['/', '/gifts/123', '/users/']) {
    assert.equal(spa({ request: { uri } }).uri, '/index.html');
  }
});
test('확장자가 있는 정적 자산 URI는 유지', () => {
  for (const uri of ['/index.html', '/assets/app.js', '/favicon.ico']) {
    assert.equal(spa({ request: { uri } }).uri, uri);
  }
});
test('API가 정적 처리로 잘못 들어와도 HTML200으로 숨기지 않음', () => {
  for (const uri of ['/api', '/api/products']) {
    assert.equal(spa({ request: { uri } }).statusCode, 404);
  }
});
