import http from 'k6/http';
import exec from 'k6/execution';
import { check } from 'k6';
import { Rate, Counter } from 'k6/metrics';
import { validatePlan, termConsents, signupEmail, performanceThresholds,
  requestLimits, canAttempt, completionThresholds } from '../lib/write-load-contract.mjs';

// Fixture는 로컬 0600 파일만 사용. Secret·전체 응답·토큰은 결과 원장에 기록하지 않는다.
if (!__ENV.WRITE_FIXTURE_FILE) throw new Error('WRITE_FIXTURE_FILE required');
const fixture = JSON.parse(open(__ENV.WRITE_FIXTURE_FILE));
const plan = validatePlan(__ENV, fixture);
const businessErrors = new Rate('comparison_business_errors');
const rateLimited = new Counter('comparison_rate_limited');
const writeAttempts = new Counter('comparison_write_attempts');
const completions = new Counter('comparison_request_completions');
const budgetSkips = new Counter('comparison_budget_skips');
const limits = requestLimits(plan.profile);

function arrival(operation, timeUnit, vus) {
  return { executor: 'constant-arrival-rate', exec: operation, rate: 1,
    timeUnit, duration: '5m', preAllocatedVUs: vus, maxVUs: vus, gracefulStop: '10s' };
}

export const options = {
  scenarios: plan.profile === 'smoke' ? {
    smoke: { executor: 'shared-iterations', exec: 'smoke', vus: 1, iterations: 1,
      maxDuration: '1m', gracefulStop: '10s' },
  } : {
    reads: { ...arrival('readProducts', '1s', 20), rate: 5 },
    logins: arrival('login', '10s', 3),
    signups: arrival('signup', '30s', 3),
    gifts: arrival('createGift', '10s', 3),
  },
  setupTimeout: '20s',
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
  userAgent: 'seonjalal-infra-comparison/1.0',
  thresholds: {
    comparison_business_errors: [
      'rate<0.005', { threshold: 'rate<0.05', abortOnFail: true, delayAbortEval: '20s' },
    ],
    'comparison_business_errors{endpoint:signup}': [
      { threshold: 'rate==0', abortOnFail: true, delayAbortEval: '10s' },
    ],
    'comparison_business_errors{endpoint:login}': [
      { threshold: 'rate==0', abortOnFail: true, delayAbortEval: '10s' },
    ],
    'comparison_business_errors{endpoint:gift_create}': [
      { threshold: 'rate==0', abortOnFail: true, delayAbortEval: '10s' },
    ],
    comparison_rate_limited: ['count==0'],
    dropped_iterations: ['count==0'],
    http_req_failed: ['rate<0.005'],
    ...performanceThresholds(plan.profile),
    ...completionThresholds(plan.profile),
    comparison_budget_skips: ['count<=4'],
  },
};

function params(endpoint, headers = {}) {
  return { headers: { Accept: 'application/json', 'Content-Type': 'application/json', ...headers },
    tags: { endpoint, target: plan.target }, timeout: '5s', redirects: 0 };
}

function request(endpoint, method, path, body, expected, valid, audit = {}, headers = {}) {
  const startedAt = new Date().toISOString();
  const response = http.request(method, plan.baseUrl + path,
    body === null ? null : JSON.stringify(body), params(endpoint, headers));
  completions.add(1, { endpoint });
  let payload = null;
  try { payload = response.json(); } catch (_) { /* 응답 원문은 출력하지 않는다. */ }
  const success = response.status === expected && payload && payload.error == null && valid(payload.data);
  businessErrors.add(!success, { endpoint });
  if (response.status === 429) rateLimited.add(1, { endpoint });
  check(response, { 'status and business response valid': () => Boolean(success) }, { endpoint });
  // URL 태그에 개별 이메일·토큰을 넣지 않고 개인정보가 있는 본문도 저장하지 않는다.
  console.log(JSON.stringify({ kind: 'infra_comparison_request', runId: plan.runId,
    target: plan.target, endpoint, startedAt, finishedAt: new Date().toISOString(),
    status: response.status, valid: Boolean(success), durationMs: response.timings.duration,
    transportErrorCode: response.error_code || null,
    businessErrorCode: /^[A-Z][A-Z0-9_]{0,63}$/.test(payload?.error?.code || '')
      ? payload.error.code : null,
    giftId: endpoint === 'gift_create' ? payload?.data?.gift?.giftId ?? null : undefined,
    createdUserId: endpoint === 'signup' ? payload?.data?.userId ?? null : undefined, ...audit }));
  if (plan.profile === 'smoke' && !success) exec.test.abort('SMOKE_FAILED_' + endpoint);
}

export function setup() {
  rateLimited.add(0);
  budgetSkips.add(0);
  const response = http.get(plan.baseUrl + '/api/auth/terms', params('signup_terms'));
  if (response.status !== 200) throw new Error('Signup terms preflight failed; no writes executed');
  let payload;
  try { payload = response.json(); } catch (_) { throw new Error('Terms JSON invalid'); }
  return { consents: termConsents(payload) };
}

export function readProducts() {
  if (!withinBudget('products_list')) return;
  request('products_list', 'GET', '/api/products?sort=POPULAR', null, 200,
    data => Array.isArray(data?.products));
}

export function login() {
  if (!withinBudget('login')) return;
  const actor = fixture.actors[exec.scenario.iterationInTest % fixture.actors.length];
  request('login', 'POST', '/api/auth/login', { email: actor.email, password: actor.password },
    200, data => typeof data?.accessToken === 'string' && data.accessToken.length > 0);
}

export function signup(data) {
  const index = exec.scenario.iterationInTest;
  if (!withinBudget('signup', plan.maxSignups)) return;
  writeAttempts.add(1, { endpoint: 'signup' });
  request('signup', 'POST', '/api/auth/signup', {
    name: 'Load Test', birth: '1995-01-01', email: signupEmail(plan.runId, index),
    password: fixture.signupPassword, termConsents: data.consents,
  }, 201, value => Number.isSafeInteger(value?.userId));
}

export function createGift() {
  const index = exec.scenario.iterationInTest;
  if (!withinBudget('gift_create', plan.maxGiftAttempts)) return;
  const actor = fixture.actors[index % fixture.actors.length];
  const product = fixture.products[index % fixture.products.length];
  const requestId = 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
    const random = Math.floor(Math.random() * 16);
    return (c === 'x' ? random : ((random & 3) | 8)).toString(16);
  });
  writeAttempts.add(1, { endpoint: 'gift_create' });
  request('gift_create', 'POST', '/api/gifts', { productId: product.productId,
    expectedUnitPrice: product.expectedUnitPrice, recipientUserId: actor.recipientUserId,
    quantity: 1 }, 201, value => Number.isSafeInteger(value?.gift?.giftId),
  { requestId, productId: product.productId, senderId: actor.userId,
    recipientUserId: actor.recipientUserId, quantity: 1, expectedUnitPrice: product.expectedUnitPrice },
  { Authorization: 'Bearer ' + actor.accessToken, 'Idempotency-Key': requestId });
}

export function smoke(data) {
  readProducts();
  login();
  signup(data);
  createGift();
}

function withinBudget(endpoint, fixtureLimit = limits[endpoint]) {
  if (canAttempt(exec.scenario.iterationInTest, Math.min(limits[endpoint], fixtureLimit))) return true;
  // 종료 경계의 추가 반복은 보내지 않고, 다른 요청의 graceful 완료는 방해하지 않는다.
  budgetSkips.add(1, { endpoint });
  console.log(JSON.stringify({ kind: 'infra_comparison_budget_skip', runId: plan.runId,
    target: plan.target, endpoint, iteration: exec.scenario.iterationInTest,
    timestamp: new Date().toISOString() }));
  return false;
}
