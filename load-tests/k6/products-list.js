import http from 'k6/http';
import { check } from 'k6';
import { Rate } from 'k6/metrics';

const PROFILE = __ENV.PROFILE || 'smoke';
const BASE_URL = (__ENV.BASE_URL || '').replace(/\/$/, '');
const IS_PRODUCTION = /^https?:\/\/(www\.)?seonjalal\.com(?::\d+)?(?:\/|$)/i.test(BASE_URL);

const businessErrors = new Rate('business_errors');

const profiles = {
  smoke: {
    scenarios: {
      products_list: {
        executor: 'shared-iterations',
        exec: 'getProducts',
        vus: 1,
        iterations: 3,
        maxDuration: '30s',
      },
    },
    thresholds: {
      'checks{endpoint:products_list}': ['rate==1'],
      'http_req_failed{endpoint:products_list}': ['rate==0'],
      'business_errors{endpoint:products_list}': ['rate==0'],
    },
  },
  baseline: {
    scenarios: {
      products_list: {
        executor: 'constant-arrival-rate',
        exec: 'getProducts',
        rate: 5,
        timeUnit: '1s',
        duration: '5m',
        preAllocatedVUs: 20,
        maxVUs: 50,
      },
    },
    thresholds: loadThresholds(),
  },
  load: {
    scenarios: {
      products_list: {
        executor: 'ramping-arrival-rate',
        exec: 'getProducts',
        startRate: 1,
        timeUnit: '1s',
        preAllocatedVUs: 60,
        maxVUs: 200,
        stages: [
          { duration: '2m', target: 5 },
          { duration: '3m', target: 10 },
          { duration: '5m', target: 30 },
          { duration: '20m', target: 30 },
          { duration: '2m', target: 0 },
        ],
      },
    },
    thresholds: loadThresholds(),
  },
};

if (!BASE_URL || !/^https?:\/\//i.test(BASE_URL)) {
  throw new Error('BASE_URL must be an http(s) URL. Example: https://staging.example.com');
}

if (!profiles[PROFILE]) {
  throw new Error(`Unknown PROFILE '${PROFILE}'. Use smoke, baseline, or load.`);
}

if (IS_PRODUCTION && PROFILE !== 'smoke' && __ENV.ALLOW_PRODUCTION !== 'true') {
  throw new Error(
    'Production baseline/load test blocked. Set ALLOW_PRODUCTION=true after completing the run checklist.',
  );
}

export const options = {
  discardResponseBodies: false,
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
  userAgent: 'seonjalal-k6-stage2/1.0',
  ...profiles[PROFILE],
};

export function getProducts() {
  const tags = { endpoint: 'products_list' };
  const response = http.get(`${BASE_URL}/api/products?sort=POPULAR`, {
    tags,
    timeout: '5s',
  });

  let validBody = false;

  if (response.status === 200) {
    try {
      const body = response.json();
      validBody =
        body !== null &&
        body.error == null &&
        body.data !== null &&
        Array.isArray(body.data.products);
    } catch (_) {
      validBody = false;
    }
  }

  businessErrors.add(!validBody, tags);

  check(
    response,
    {
      'status is 200': (res) => res.status === 200,
      'response has products array': () => validBody,
    },
    tags,
  );
}

function loadThresholds() {
  return {
    'checks{endpoint:products_list}': [
      'rate>0.99',
      {
        threshold: 'rate>0.95',
        abortOnFail: true,
        delayAbortEval: '30s',
      },
    ],
    'http_req_failed{endpoint:products_list}': [
      'rate<0.01',
      {
        threshold: 'rate<0.05',
        abortOnFail: true,
        delayAbortEval: '30s',
      },
    ],
    'http_req_duration{endpoint:products_list}': [
      'p(95)<500',
      'p(99)<1000',
    ],
    'business_errors{endpoint:products_list}': ['rate<0.01'],
    dropped_iterations: ['count==0'],
  };
}
