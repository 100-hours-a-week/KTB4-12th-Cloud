import http from 'k6/http';
import encoding from 'k6/encoding';
import { check } from 'k6';
import { Counter } from 'k6/metrics';

import { BASE_URL, IS_PRODUCTION, PROFILE } from './config.js';
import { signHs256 } from './jwt.js';
import { businessErrors } from './metrics.js';
import { apiTestOptions } from './profiles.js';

// V2 스테이징 전용 부하 시험 공통 코드.
// V1 코드(config.js의 직접 JWT 방식)는 건드리지 않는다. 환경변수 이름도 달리해서 V1 경로가 켜지지 않게 한다.

export const STAGING_URL = 'https://v2-staging.seonjalal.com';
// 로컬 모의 서버 시험용. 실제 서버로는 보내지 않는다.
const LOCAL_MOCK_URL = /^http:\/\/127\.0\.0\.1:[0-9]+$/;

const SECRET_BASE64 = String(__ENV.STAGING_JWT_SECRET_BASE64 || '').trim();
const ISSUER = String(__ENV.STAGING_JWT_ISSUER || '').trim();
const GATE_TOKEN = String(__ENV.STAGING_GATE_TOKEN || '').trim();
const USER_ID_OFFSET = Number(__ENV.V2_USER_ID_OFFSET);
const USER_COUNT = Number(__ENV.V2_USER_COUNT || 10000);

export const expectedBusinessErrors = new Counter('v2_expected_business_errors');

let signingKey = null;
const tokenCache = {};

export function targetAllowed() {
  if (IS_PRODUCTION) {
    return false;
  }
  if (BASE_URL.toLowerCase() === STAGING_URL) {
    return true;
  }
  return __ENV.V2_ALLOW_LOCAL_MOCK === 'true' && LOCAL_MOCK_URL.test(BASE_URL);
}

export function assertAllowedTarget() {
  if (!targetAllowed()) {
    throw new Error(`V2 load tests run only against ${STAGING_URL}. Production and other hosts are blocked.`);
  }
  if (GATE_TOKEN === '') {
    throw new Error('STAGING_GATE_TOKEN is required (sent as the stg_gate cookie). Never commit it.');
  }
}

export function validateV2Environment() {
  assertAllowedTarget();
  if (__ENV.ALLOW_DIRECT_JWT_STAGING !== 'true') {
    throw new Error('Set ALLOW_DIRECT_JWT_STAGING=true to mint staging test tokens with the signing secret.');
  }
  if (SECRET_BASE64 === '' || ISSUER === '') {
    throw new Error('STAGING_JWT_SECRET_BASE64 and STAGING_JWT_ISSUER are required. Never commit them.');
  }
  if (!Number.isInteger(USER_ID_OFFSET) || USER_ID_OFFSET < 0) {
    throw new Error('V2_USER_ID_OFFSET must be a non-negative integer (user id = offset + loadtest number).');
  }
  if (!Number.isInteger(USER_COUNT) || USER_COUNT < 1 || USER_COUNT > 10000) {
    throw new Error('V2_USER_COUNT must be an integer between 1 and 10000.');
  }
  getSigningKey();
}

function getSigningKey() {
  if (signingKey === null) {
    try {
      signingKey = encoding.b64decode(SECRET_BASE64, 'std');
    } catch (_) {
      throw new Error('STAGING_JWT_SECRET_BASE64 must be valid standard Base64.');
    }
    if (signingKey.byteLength < 32) {
      throw new Error('The JWT signing key must decode to at least 32 bytes.');
    }
  }
  return signingKey;
}

// 사용자 번호(1..USER_COUNT)를 반복마다 고르게 퍼뜨린다. 7919와 104729는 10000과 서로소다.
export function currentUserNumber() {
  return (((__VU - 1) * 7919 + __ITER * 104729) % USER_COUNT) + 1;
}

export function userIdForNumber(userNumber) {
  return USER_ID_OFFSET + userNumber;
}

export function currentUserId() {
  return userIdForNumber(currentUserNumber());
}

export function userCount() {
  return USER_COUNT;
}

export function tokenForUserNumber(userNumber) {
  return tokenForUserId(userIdForNumber(userNumber));
}

// 서명 비밀이 있으면 어떤 사용자의 토큰도 만들 수 있다. 친구 요청을 수락하거나 선물을 수락하는 '상대방' 토큰에도 쓴다.
export function tokenForUserId(userId) {
  const now = Math.floor(Date.now() / 1000);
  const cached = tokenCache[userId];
  if (cached && cached.expiresAt - now > 300) {
    return cached.token;
  }
  // 서버는 iat가 미래이면 거부한다(여유 없음). 시계 오차를 막으려고 30초 앞당긴다.
  const issuedAt = now - 30;
  const expiresAt = issuedAt + 3600;
  const token = signHs256({
    subject: String(userId),
    issuer: ISSUER,
    issuedAt,
    expiresAt,
    signingKey: getSigningKey(),
  });
  tokenCache[userId] = { token, expiresAt };
  return token;
}

export function gateHeaders() {
  return { Cookie: `stg_gate=${GATE_TOKEN}` };
}

// 성공(successStatuses)과 정상적인 업무 오류(expectedErrorStatuses)를 구분한다.
// 정상 업무 오류(예: 이미 처리된 요청의 409)는 실패로 세지 않고 별도 카운터에 기록한다.
export function v2Request({
  method = 'GET',
  path,
  endpoint,
  workload = 'v2',
  body,
  headers = {},
  validate,
  onSuccess,
  successStatuses = [200],
  expectedErrorStatuses: allowedErrorStatuses = [],
  userId = currentUserId(),
}) {
  // 스모크에서는 정상 업무 오류(409, 422 등)를 허용하지 않는다. 계약이 틀리면 바로 드러나게 하기 위해서다.
  const expectedErrorStatuses = isSmokeProfile() ? [] : allowedErrorStatuses;
  const tags = { endpoint, workload, rw: method === 'GET' ? 'read' : 'write' };
  const requestHeaders = {
    Accept: 'application/json',
    Authorization: `Bearer ${tokenForUserId(userId)}`,
    ...gateHeaders(),
    ...headers,
  };
  const params = {
    headers: requestHeaders,
    tags,
    timeout: '5s',
    responseCallback: http.expectedStatuses(...successStatuses, ...expectedErrorStatuses),
  };

  let response;
  if (body !== undefined) {
    requestHeaders['Content-Type'] = 'application/json';
    response = http.request(method, `${BASE_URL}${path}`, JSON.stringify(body), params);
  } else {
    response = http.request(method, `${BASE_URL}${path}`, null, params);
  }

  const isSuccess = successStatuses.includes(response.status);
  const isExpectedError = !isSuccess && expectedErrorStatuses.includes(response.status);

  let valid = false;
  if (isSuccess) {
    try {
      const parsed = response.json();
      const data = parsed?.data ?? null;
      valid = parsed?.error == null && validate(data, parsed);
      if (valid && onSuccess) {
        onSuccess(data, tags);
      }
    } catch (_) {
      valid = false;
    }
  }
  if (isExpectedError) {
    expectedBusinessErrors.add(1, tags);
  }

  businessErrors.add(!(valid || isExpectedError), tags);
  check(
    response,
    {
      [`${endpoint}: status is expected`]: () => isSuccess || isExpectedError,
      [`${endpoint}: response contract is valid`]: () => valid || isExpectedError,
    },
    tags,
  );
  return { response, valid, isExpectedError };
}

function normalizeProfile(profile) {
  if (profile === 'load') return 'capacity';
  if (profile === 'stress') return 'breakpoint';
  return profile;
}

export function isSmokeProfile() {
  return normalizeProfile(PROFILE) === 'smoke';
}

// 스테이징 ALB는 운영과 공용이다. 30 RPS를 넘는 profile(spike 60, breakpoint 최대 160)은 명시 승인 변수가 있어야 한다.
export function guardSharedAlb() {
  const profile = normalizeProfile(PROFILE);
  if ((profile === 'spike' || profile === 'breakpoint') && __ENV.ALLOW_ABOVE_30_RPS !== 'true') {
    throw new Error(
      `PROFILE=${PROFILE} exceeds 30 RPS and the staging ALB is shared with production. Set ALLOW_ABOVE_30_RPS=true only after approval.`,
    );
  }
}

export function v2Options({ endpoint, exec, changesData = false }) {
  validateV2Environment();
  guardSharedAlb();
  return apiTestOptions({ endpoint, exec, authenticated: false, changesData });
}

export function v2WorkloadOptions({ workload, exec, smokeExec, rwThresholds = false }) {
  validateV2Environment();
  guardSharedAlb();
  const profile = normalizeProfile(PROFILE);
  const tag = { workload };
  const thresholds = {
    [`checks{workload:${workload}}`]: [
      'rate>0.995',
      { threshold: 'rate>0.95', abortOnFail: true, delayAbortEval: '30s' },
    ],
    [`http_req_failed{workload:${workload}}`]: [
      'rate<0.005',
      { threshold: 'rate<0.05', abortOnFail: true, delayAbortEval: '30s' },
    ],
    [`business_errors{workload:${workload}}`]: ['rate<0.005'],
    [`http_req_duration{workload:${workload}}`]: ['p(95)<500', 'p(99)<1000'],
    dropped_iterations: ['count==0'],
  };
  if (rwThresholds) {
    // 읽기와 쓰기를 따로 판정한다(쓰기 기준은 V1 잠정 SLO: p95 800ms, p99 1.5s).
    delete thresholds[`http_req_duration{workload:${workload}}`];
    thresholds[`http_req_duration{workload:${workload},rw:read}`] = ['p(95)<500', 'p(99)<1000'];
    thresholds[`http_req_duration{workload:${workload},rw:write}`] = ['p(95)<800', 'p(99)<1500'];
  }
  if (profile === 'breakpoint' && rwThresholds) {
    // 한계를 찾는 시험이라 임계값 위반 자체는 결과다. 다만 서비스가 무너진 뒤 계속 밀지 않도록 지연이 2초를 넘어 30초 지속되면 중단한다.
    thresholds[`http_req_duration{workload:${workload},rw:read}`].push({
      threshold: 'p(95)<2000',
      abortOnFail: true,
      delayAbortEval: '30s',
    });
    thresholds[`http_req_duration{workload:${workload},rw:write}`].push({
      threshold: 'p(95)<3000',
      abortOnFail: true,
      delayAbortEval: '30s',
    });
  }
  const base = {
    discardResponseBodies: false,
    summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
    userAgent: 'seonjalal-k6-v2/1.0',
  };

  if (profile === 'smoke') {
    return {
      ...base,
      scenarios: {
        [`${workload}_smoke`]: {
          executor: 'shared-iterations',
          exec: smokeExec,
          vus: 1,
          iterations: 1,
          maxDuration: '1m',
          tags: { ...tag, test_type: 'smoke' },
        },
      },
      thresholds: {
        [`checks{workload:${workload}}`]: ['rate==1'],
        [`http_req_failed{workload:${workload}}`]: ['rate==0'],
        [`business_errors{workload:${workload}}`]: ['rate==0'],
      },
    };
  }

  // 로컬 모의 서버에서 읽기:쓰기 비율을 확인하는 용도. 실제 스테이징에서는 쓸 수 없다.
  if (profile === 'mockcheck') {
    if (!(__ENV.V2_ALLOW_LOCAL_MOCK === 'true' && LOCAL_MOCK_URL.test(BASE_URL))) {
      throw new Error("PROFILE=mockcheck runs only against the local mock server.");
    }
    return {
      ...base,
      scenarios: {
        [workload]: {
          executor: 'shared-iterations',
          exec,
          vus: 10,
          iterations: Number(__ENV.V2_MOCKCHECK_ITERATIONS || 2000),
          maxDuration: '5m',
          tags: { ...tag, test_type: 'mockcheck' },
        },
      },
    };
  }

  const arrival = (executor, configuration) => ({
    executor,
    exec,
    timeUnit: '1s',
    tags: { ...tag, test_type: profile },
    ...configuration,
  });

  const scenarios = {
    baseline: () => arrival('constant-arrival-rate', { rate: 5, duration: '5m', preAllocatedVUs: 20, maxVUs: 50 }),
    capacity: () =>
      arrival('ramping-arrival-rate', {
        startRate: 1,
        preAllocatedVUs: 60,
        maxVUs: 200,
        stages: [
          { duration: '2m', target: 5 },
          { duration: '3m', target: 10 },
          { duration: '5m', target: 30 },
          { duration: '20m', target: 30 },
          { duration: '2m', target: 0 },
        ],
      }),
    // 단계 상승: 단계마다 30초 램프 후 3분 유지. 1단계(30)는 통과가 확인된 값이다. 최대 160 반복/초.
    breakpoint: () =>
      arrival('ramping-arrival-rate', {
        startRate: 5,
        preAllocatedVUs: 200,
        maxVUs: 600,
        stages: [
          { duration: '1m', target: 30 },
          { duration: '2m', target: 30 },
          ...[45, 60, 80, 100, 130, 160].flatMap((target) => [
            { duration: '30s', target },
            { duration: '3m', target },
          ]),
          { duration: '1m', target: 0 },
        ],
      }),
    spike: () =>
      arrival('ramping-arrival-rate', {
        startRate: 5,
        preAllocatedVUs: 100,
        maxVUs: 300,
        stages: [
          { duration: '1m', target: 5 },
          { duration: '10s', target: 60 },
          { duration: '1m', target: 60 },
          { duration: '10s', target: 5 },
          { duration: '2m', target: 5 },
          { duration: '30s', target: 0 },
        ],
      }),
  };
  if (!scenarios[profile]) {
    throw new Error(`Unsupported PROFILE '${PROFILE}' for the V2 workload. Use smoke, baseline, capacity, spike, or breakpoint.`);
  }
  return { ...base, scenarios: { [workload]: scenarios[profile]() }, thresholds };
}
