import { getJson, postJson } from '../lib/http.js';
import {
  ACCESS_TOKENS, BASE_URL, PROFILE, requireAccessToken, requireEnv,
  requirePositiveInteger, validateBaseUrl,
} from '../lib/config.js';

validateBaseUrl();
// 실제 운영/다른 서버에 쓰기 요청이 전송되지 않도록 기존 V1 시험 도메인만 허용한다.
if (BASE_URL !== 'https://v1-benchmark.seonjalal.com') {
  throw new Error('This rehearsal requires https://v1-benchmark.seonjalal.com.');
}
if (__ENV.ALLOW_REHEARSAL_WRITES !== '1') {
  throw new Error('Set ALLOW_REHEARSAL_WRITES=1 after confirming the test data and side effects.');
}
if (!['smoke', 'baseline'].includes(PROFILE)) {
  throw new Error('Use smoke or baseline. Spike/stress are outside this first replication test.');
}
requireAccessToken();
const runId = requireEnv('MIGRATION_RUN_ID');
if (!/^[A-Za-z0-9_-]{1,80}$/.test(runId)) throw new Error('Invalid MIGRATION_RUN_ID.');
const productId = requirePositiveInteger('PRODUCT_ID');
const recipientUserId = requirePositiveInteger('RECIPIENT_USER_ID');
const expectedUnitPrice = Number(requireEnv('EXPECTED_UNIT_PRICE'));
if (!Number.isInteger(expectedUnitPrice) || expectedUnitPrice < 0) {
  throw new Error('EXPECTED_UNIT_PRICE must be a non-negative integer.');
}
const includeLogin = __ENV.MIGRATION_INCLUDE_LOGIN === '1';
const loginEmail = includeLogin ? requireEnv('LOGIN_EMAIL') : '';
const loginPassword = includeLogin ? requireEnv('LOGIN_PASSWORD') : '';

function arrival(exec, timeUnit, vus) {
  return {
    executor: 'constant-arrival-rate', exec, rate: 1, timeUnit,
    duration: '10m', preAllocatedVUs: vus, maxVUs: vus, gracefulStop: '10s',
  };
}

export const options = {
  scenarios: PROFILE === 'smoke' ? {
    migration_smoke: {
      executor: 'shared-iterations', exec: 'smoke', vus: 1, iterations: 1,
      maxDuration: '1m',
    },
  } : {
    reads: arrival('readProducts', '1s', 10),
    gifts: arrival('writeGift', '10s', 2),
    ...(includeLogin ? { logins: arrival('login', '1m', 1) } : {}),
  },
  thresholds: {
    checks: ['rate==1'], http_req_failed: ['rate==0'],
    business_errors: ['rate==0'], dropped_iterations: ['count==0'],
  },
};

// 토큰·비밀번호·응답 본문은 기록하지 않고 실패·타임아웃을 포함한 요청 결과를 남긴다.
function audit(endpoint, startedAt, result, extra = {}) {
  console.log(JSON.stringify({
    kind: 'migration_request', runId, endpoint,
    startedAt, finishedAt: new Date().toISOString(),
    status: result.response.status, valid: result.valid,
    errorCode: result.response.error_code || null, ...extra,
  }));
}

export function readProducts() {
  const startedAt = new Date().toISOString();
  const result = getJson({
    path: '/api/products?sort=POPULAR', endpoint: 'products_list',
    validateData: (data) => Array.isArray(data?.products),
  });
  audit('products_list', startedAt, result);
}

export function writeGift() {
  const startedAt = new Date().toISOString();
  const requestId = uuid();
  const result = postJson({
    path: '/api/gifts', endpoint: 'gift_create', expectedStatus: 201,
    // 고정 수신자는 첫 토큰 사용자의 친구이므로 쓰기 VU가 늘어도 발신자를 바꾸지 않는다.
    authenticated: false,
    headers: {
      Authorization: `Bearer ${ACCESS_TOKENS[0]}`,
      'Idempotency-Key': requestId,
    },
    body: { productId, recipientUserId, quantity: 1, expectedUnitPrice },
    validateData: (data) => Number.isInteger(data?.gift?.giftId),
  });
  // 성공한 ID는 이후 Source/Target과 대조한다. 실패·타임아웃을 미Commit으로 단정하지 않는다.
  audit('gift_create', startedAt, result, {
    requestId, giftId: result.data?.gift?.giftId ?? null,
    productId, recipientUserId, quantity: 1, expectedUnitPrice,
  });
}

export function login() {
  const startedAt = new Date().toISOString();
  const result = postJson({
    path: '/api/auth/login', endpoint: 'login', authenticated: false,
    body: { email: loginEmail, password: loginPassword },
    validateData: (data) => typeof data?.accessToken === 'string'
      && data.accessToken.length > 0,
  });
  audit('login', startedAt, result);
}

export function smoke() {
  readProducts();
  if (includeLogin) login();
  writeGift();
}

function uuid() {
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (character) => {
    const value = Math.floor(Math.random() * 16);
    return (character === 'x' ? value : (value & 3) | 8).toString(16);
  });
}
