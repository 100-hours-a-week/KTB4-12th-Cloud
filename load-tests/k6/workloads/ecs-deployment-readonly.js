// 스테이징 상품 목록만 조회. 로그인/선물/상세 조회/재시도/쓰기 요청 없음.
import http from 'k6/http';
import { Counter, Rate } from 'k6/metrics';
import { classifyCutoverRead } from '../lib/migration-cutover-contract.js';

const baseUrl = __ENV.BASE_URL;
const profile = __ENV.PROFILE;
const runId = __ENV.ECS_RUN_ID;
if (baseUrl !== 'https://v2-staging.seonjalal.com') throw new Error('Only the approved staging URL is allowed.');
if (!['smoke', 'baseline'].includes(profile)) throw new Error('Only smoke/baseline is allowed.');
if (!/^[0-9]{8}T[0-9]{6}Z-ecs-readonly-(smoke|baseline)-[0-9]+$/.test(runId || '')) throw new Error('Invalid ECS run ID.');

const requests = new Counter('ecs_read_requests');
const errors = new Rate('ecs_read_errors');
export const options = {
  insecureSkipTLSVerify: false,
  scenarios: profile === 'smoke' ? {
    reads: { executor: 'shared-iterations', vus: 1, iterations: 1, maxDuration: '30s' },
  } : {
    reads: { executor: 'constant-arrival-rate', rate: 1, timeUnit: '1s', duration: '20m', preAllocatedVUs: 10, maxVUs: 10, gracefulStop: '5s' },
  },
  thresholds: {
    ecs_read_requests: ['count>0'],
    ecs_read_errors: ['rate==0'],
    http_req_failed: ['rate==0'],
    dropped_iterations: ['count==0'],
  },
};

export default function () {
  const startedAt = new Date().toISOString();
  const response = http.get(`${baseUrl}/products?sort=POPULAR`, {
    timeout: '5s', redirects: 0, headers: { Accept: 'application/json' },
    tags: { endpoint: 'products_list' },
  });
  let body = null;
  try { body = response.json(); } catch (_) { /* JSON 오류도 실패로 집계 */ }
  const valid = classifyCutoverRead(response.status, body) === 'read_success';
  requests.add(1);
  errors.add(!valid);
  console.log(JSON.stringify({
    kind: 'ecs_read_request', runId, startedAt, finishedAt: new Date().toISOString(),
    status: response.status, valid, durationMs: response.timings.duration,
    transportErrorCode: response.error_code || null,
  }));
}
