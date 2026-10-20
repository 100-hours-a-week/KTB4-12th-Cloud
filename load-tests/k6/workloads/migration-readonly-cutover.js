import http from 'k6/http';
import { Counter, Rate } from 'k6/metrics';
import {
  ACCESS_TOKENS, BASE_URL, PROFILE, requireAccessToken, requireEnv,
  requirePositiveInteger, validateBaseUrl,
} from '../lib/config.js';
import { classifyCutoverRead, classifyCutoverGift } from '../lib/migration-cutover-contract.js';

validateBaseUrl();
requireAccessToken();
if (BASE_URL !== 'https://v1-benchmark.seonjalal.com' || __ENV.ALLOW_REHEARSAL_WRITES !== '1') {
  throw new Error('Only the approved V1 benchmark rehearsal is allowed.');
}
if (!['smoke', 'baseline'].includes(PROFILE) || __ENV.JWT_SECRET_BASE64) {
  throw new Error('Use smoke/baseline and a real login token.');
}
const allowFreeze = PROFILE === 'baseline' && __ENV.MIGRATION_EXPECT_WRITE_FREEZE === '1';
if (PROFILE === 'baseline' && !allowFreeze) {
  throw new Error('Explicit MIGRATION_EXPECT_WRITE_FREEZE=1 is required for this cutover test.');
}
const runId = requireEnv('MIGRATION_RUN_ID');
if (!/^[A-Za-z0-9_-]{1,80}$/.test(runId)) throw new Error('Invalid run ID.');
const productId = requirePositiveInteger('PRODUCT_ID');
const recipientUserId = requirePositiveInteger('RECIPIENT_USER_ID');
const price = Number(requireEnv('EXPECTED_UNIT_PRICE'));
if (!Number.isSafeInteger(price) || price < 0) throw new Error('Invalid unit price.');

const readErrors = new Rate('cutover_read_errors');
const unexpectedWriteErrors = new Rate('cutover_unexpected_write_errors');
const readRequests = new Counter('cutover_read_requests');
const writeSuccesses = new Counter('cutover_write_successes');
const frozenWrites = new Counter('cutover_write_rejections');
const writeSuccessRate = new Rate('cutover_write_success_rate');

function arrival(exec, timeUnit, vus) {
  return {
    executor: 'constant-arrival-rate', exec, rate: 1, timeUnit,
    duration: '10m', preAllocatedVUs: vus, maxVUs: vus, gracefulStop: '10s',
  };
}

export const options = {
  scenarios: PROFILE === 'smoke' ? {
    cutover_smoke: { executor: 'shared-iterations', exec: 'smoke', vus: 1, iterations: 1, maxDuration: '1m' },
  } : {
    reads: arrival('readProducts', '1s', 10),
    gifts: arrival('writeGift', '10s', 2),
  },
  thresholds: {
    cutover_read_errors: ['rate==0'],
    cutover_unexpected_write_errors: ['rate==0'],
    cutover_read_requests: ['count>0'],
    cutover_write_successes: ['count>0'],
    dropped_iterations: ['count==0'],
  },
};

function decode(response) {
  try { return response.json(); } catch (_) { return null; }
}

function audit(endpoint, startedAt, response, body, outcome, extra = {}) {
  console.log(JSON.stringify({
    kind: 'migration_request', runId, endpoint, startedAt,
    finishedAt: new Date().toISOString(), status: response.status,
    valid: outcome === 'read_success' || outcome === 'gift_success',
    outcome, errorCode: body?.error?.code ?? null,
    transportErrorCode: response.error_code || null, ...extra,
  }));
}

export function readProducts() {
  const startedAt = new Date().toISOString();
  const response = http.get(`${BASE_URL}/api/products?sort=POPULAR`, {
    timeout: '5s', redirects: 0, tags: { endpoint: 'products_list' },
    headers: { Accept: 'application/json' },
  });
  const body = decode(response);
  const outcome = classifyCutoverRead(response.status, body);
  readRequests.add(1);
  readErrors.add(outcome !== 'read_success');
  audit('products_list', startedAt, response, body, outcome);
}

export function writeGift() {
  const startedAt = new Date().toISOString();
  const requestId = 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (char) => {
    const value = Math.floor(Math.random() * 16);
    return (char === 'x' ? value : (value & 3) | 8).toString(16);
  });
  const response = http.post(`${BASE_URL}/api/gifts`, JSON.stringify({
    productId, recipientUserId, quantity: 1, expectedUnitPrice: price,
  }), {
    timeout: '5s', redirects: 0, tags: { endpoint: 'gift_create' },
    headers: {
      Accept: 'application/json', 'Content-Type': 'application/json',
      Authorization: `Bearer ${ACCESS_TOKENS[0]}`, 'Idempotency-Key': requestId,
    },
  });
  const body = decode(response);
  const outcome = classifyCutoverGift(response.status, body);
  const success = outcome === 'gift_success';
  writeSuccesses.add(success ? 1 : 0);
  frozenWrites.add(outcome === 'write_frozen' ? 1 : 0);
  writeSuccessRate.add(success);
  unexpectedWriteErrors.add(!success && !(allowFreeze && outcome === 'write_frozen'));
  // 503을 HTTP 성공으로 바꾸거나 재전송하지 않는다. 기본 http_req_failed에도 그대로 남긴다.
  audit('gift_create', startedAt, response, body, outcome, {
    requestId, giftId: success ? body.data.gift.giftId : null,
    productId, recipientUserId, quantity: 1, expectedUnitPrice: price,
  });
}

export function smoke() {
  readProducts();
  writeGift();
}
