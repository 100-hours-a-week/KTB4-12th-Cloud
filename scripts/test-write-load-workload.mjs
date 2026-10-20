// k6 모듈을 로컬에서 모의해 실제 HTTP/DB 변경 없이 종료 경계를 검사한다.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { test } from 'node:test';

async function harness() {
  const calls = [], logs = [], samples = [], aborts = [];
  const fixture = { target: 'v1', friendshipsVerified: true, stockVerified: true, sideEffectsReviewed: true,
    actors: [{ email: 'loadtest001@seonjalal.invalid', password: 'Synthetic123!',
      accessToken: 'fake.token.signature', userId: 524, recipientUserId: 577 }],
    products: [{ productId: 4, expectedUnitPrice: 22900 }], signupPassword: 'Synthetic123!',
    maxSignups: 10, maxGiftAttempts: 30 };
  const execution = { scenario: { iterationInTest: 0 }, test: { abort(reason) {
    aborts.push(reason); throw new Error(reason);
  } } };
  const context = vm.createContext({
    __ENV: { LOAD_TARGET: 'v1', BASE_URL: 'https://v1-benchmark.seonjalal.com',
      PROFILE: 'baseline', LOAD_RUN_ID: 'synthetic-boundary-test', ALLOW_COMPARISON_WRITES: '1',
      WRITE_FIXTURE_FILE: '/synthetic/fixture.json' },
    open: () => JSON.stringify(fixture), console: { log: value => logs.push(JSON.parse(value)) },
  });
  class Metric {
    constructor(name) { this.name = name; }
    add(value, tags) { samples.push({ name: this.name, value, tags }); }
  }
  const http = { request(method, url, body) {
    calls.push({ method, url, body });
    const created = url.endsWith('/gifts') || url.endsWith('/signup');
    return { status: created ? 201 : 200, timings: { duration: 1 }, error_code: 0,
      json: () => ({ data: { products: [], accessToken: 'synthetic', userId: 1234, gift: { giftId: 9999 } } }) };
  } };
  const modules = {};
  for (const [name, exports] of Object.entries({
    'k6/http': { default: http }, 'k6/execution': { default: execution },
    k6: { check: () => true }, 'k6/metrics': { Rate: Metric, Counter: Metric },
  })) {
    modules[name] = new vm.SyntheticModule(Object.keys(exports), function () {
      for (const [key, value] of Object.entries(exports)) this.setExport(key, value);
    }, { context });
  }
  const library = new vm.SourceTextModule(readFileSync(new URL('../load-tests/k6/lib/write-load-contract.mjs', import.meta.url), 'utf8'), { context });
  const workload = new vm.SourceTextModule(readFileSync(new URL('../load-tests/k6/workloads/infra-write-comparison.js', import.meta.url), 'utf8'), { context });
  await workload.link(specifier => specifier.startsWith('../lib/') ? library : modules[specifier]);
  await workload.evaluate();
  return { workload: workload.namespace, execution, calls, logs, samples, aborts };
}

test('gift iteration 29 sends exactly one request', async () => {
  const h = await harness(); h.execution.scenario.iterationInTest = 29;
  h.workload.createGift(); assert.equal(h.calls.length, 1); assert.equal(h.aborts.length, 0);
});
test('gift iteration 30 sends no request and does not abort other scenarios', async () => {
  const h = await harness(); h.execution.scenario.iterationInTest = 30;
  h.workload.createGift(); assert.equal(h.calls.length, 0); assert.equal(h.aborts.length, 0);
  assert.equal(h.logs[0].kind, 'infra_comparison_budget_skip');
  h.execution.scenario.iterationInTest = 100;
  h.workload.readProducts(); assert.equal(h.calls.length, 1);
});
test('signup at its exact budget sends no request', async () => {
  const h = await harness(); h.execution.scenario.iterationInTest = 10;
  h.workload.signup({ consents: [] }); assert.equal(h.calls.length, 0); assert.equal(h.aborts.length, 0);
});
test('read and login end-boundary requests are also capped', async () => {
  const h = await harness(); h.execution.scenario.iterationInTest = 1500;
  h.workload.readProducts(); h.execution.scenario.iterationInTest = 30;
  h.workload.login(); assert.equal(h.calls.length, 0); assert.equal(h.aborts.length, 0);
});
test('actual request completion is counted independently from k6 iterations', async () => {
  const h = await harness(); h.workload.createGift();
  assert.equal(h.samples.filter(s => s.name === 'comparison_request_completions' && s.tags.endpoint === 'gift_create').length, 1);
});
