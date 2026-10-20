// SSM 전용 진입점의 상품 읽기만 검증. 인증·업무 쓰기·자동 재시도 없음.
import http from 'k6/http';
import { Counter, Rate } from 'k6/metrics';
import { classifyCutoverRead } from '../lib/migration-cutover-contract.js';

const baseUrl = __ENV.BASE_URL;
const profile = __ENV.PROFILE;
const runId = __ENV.ECS_RUN_ID;
if (baseUrl !== 'http://127.0.0.1:18084') throw new Error('Only the isolated SSM loopback URL is allowed.');
if (!['smoke', 'baseline'].includes(profile)) throw new Error('Only smoke/baseline is allowed.');
if (!/^\d{8}T\d{6}Z-v1-ecs-readonly-(smoke|baseline)-\d+$/.test(runId || '')) throw new Error('Invalid isolated run ID.');
const requests = new Counter('ecs_read_requests');
const errors = new Rate('ecs_read_errors');
export const options = {
  scenarios: profile === 'smoke' ? {
    reads: { executor: 'shared-iterations', vus: 1, iterations: 1, maxDuration: '30s' },
  } : {
    reads: { executor: 'constant-arrival-rate', rate: 1, timeUnit: '1s', duration: '5m', preAllocatedVUs: 10, maxVUs: 10, gracefulStop: '5s' },
  },
  thresholds: {
    ecs_read_requests: ['count>0'], ecs_read_errors: ['rate==0'],
    http_req_failed: ['rate==0'], dropped_iterations: ['count==0'],
  },
};
export default function () {
  const startedAt = new Date().toISOString();
  const response = http.get(`${baseUrl}/products?sort=POPULAR`, {
    timeout: '5s', redirects: 0, headers: { Accept: 'application/json' },
    tags: { endpoint: 'products_list_isolated_cutover' },
  });
  let body = null;
  try { body = response.json(); } catch (_) { /* JSON 오류도 실패 */ }
  const upstream = response.headers['X-Rehearsal-Upstream'] || null;
  const valid = classifyCutoverRead(response.status, body) === 'read_success' && ['v1', 'ecs'].includes(upstream);
  requests.add(1);
  errors.add(!valid);
  // 기존 공통 통계 파서가 읽을 수 있는 형식에 실제 경로 표식 추가.
  console.log(JSON.stringify({
    kind: 'ecs_read_request', runId, startedAt, finishedAt: new Date().toISOString(),
    status: response.status, valid, upstream, durationMs: response.timings.duration,
    transportErrorCode: response.error_code || null,
  }));
}
