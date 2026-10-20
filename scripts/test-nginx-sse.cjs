// 이슈 #22: SSE 설정 추가가 기존 프록시·헤더·타임아웃 계약을 바꾸지 않는지 정적 검사.
// 실제 nginx -t 및 스트림 전달 검증을 대신하지 않는다.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const root = path.resolve(__dirname, '..');
const targets = [
  { file: 'operations/develop/runtime/nginx/default.conf', upstream: 'main-backend', develop: true },
  { file: 'nginx/conf.d/default.conf', upstream: 'backend', develop: false },
];

for (const { file, upstream, develop } of targets) {
  const text = fs.readFileSync(path.join(root, file), 'utf8');
  const match = text.match(/location \/api\/ \{([\s\S]*?)^\s*\}/m);
  test(`${file}: 일반 API 블록 존재`, () => assert.ok(match));
  const api = match ? match[1] : '';

  test(`${file}: SSE 지시어 세 개만 한 번씩 명시`, () => {
    for (const directive of [
      'proxy_set_header Connection "";',
      'proxy_buffering off;',
      'proxy_cache off;',
    ]) {
      assert.equal(api.split(directive).length - 1, 1, directive);
    }
    assert.doesNotMatch(text, /chunked_transfer_encoding\s/);
  });

  test(`${file}: 기존 upstream·HTTP·헤더 유지`, () => {
    assert.ok(api.includes(`proxy_pass http://${upstream}:8080/;`));
    for (const directive of [
      'proxy_http_version 1.1;',
      'proxy_set_header Host $host;',
      'proxy_set_header X-Forwarded-Proto $scheme;',
      'proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;',
      develop
        ? 'proxy_set_header X-Forwarded-Host $host;'
        : 'proxy_set_header X-Real-IP $remote_addr;',
    ]) assert.ok(api.includes(directive), directive);
  });

  test(`${file}: 기존 timeout·화면·health 계약 유지`, () => {
    const timeouts = [...text.matchAll(/^\s*(proxy_\w+_timeout\s+[^;]+;)/gm)].map(x => x[1]);
    assert.deepEqual(timeouts, develop
      ? ['proxy_connect_timeout 5s;', 'proxy_read_timeout 30s;']
      : []);
    assert.ok(text.includes('try_files $uri $uri/ /index.html;'));
    if (!develop) {
      const health = text.match(/location = \/api\/health \{([\s\S]*?)^\s*\}/m);
      assert.ok(health);
      assert.ok(health[1].includes('proxy_pass http://backend:8080/actuator/health;'));
      assert.doesNotMatch(health[1], /proxy_buffering|proxy_cache|proxy_set_header Connection/);
    }
  });
}
