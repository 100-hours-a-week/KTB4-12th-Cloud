import http from 'k6/http';
import { check } from 'k6';
import { Rate } from 'k6/metrics';

import {
  accessTokenForVu,
  BASE_URL,
  IS_PRODUCTION,
  PROFILE,
  requireEnv,
  requirePositiveInteger,
  validateBaseUrl,
} from '../lib/config.js';

const PRODUCT_ID = requirePositiveInteger('PRODUCT_ID');
const RECEIVED_GIFT_ID = requirePositiveInteger('RECEIVED_GIFT_ID');
const RECIPIENT_USER_ID = requirePositiveInteger('RECIPIENT_USER_ID');
const SEARCH_QUERY = encodeURIComponent(requireEnv('SEARCH_QUERY'));
const normalizedProfile = normalizeProfile(PROFILE);

const businessErrors = new Rate('business_errors');

const weightedRequests = [
  request('product_categories', 5, '/api/products/categories', categories),
  request('products_list', 30, '/api/products?sort=POPULAR', products),
  request('product_detail', 25, `/api/products/${PRODUCT_ID}`, productDetail),
  request('user_profile', 10, '/api/users/me', userProfile),
  request('friends_list', 10, '/api/friends', friends),
  request('friends_search', 5, `/api/friends/search?query=${SEARCH_QUERY}`, friends),
  request('received_gifts_list', 10, '/api/gifts/received', giftList),
  request(
    'received_gift_detail',
    4,
    `/api/gifts/received/${RECEIVED_GIFT_ID}`,
    giftDetail,
  ),
  request('gift_preflight', 1, '/api/gifts/preflight', objectData, {
    method: 'POST',
    body: {
      productId: PRODUCT_ID,
      recipientUserId: RECIPIENT_USER_ID,
      quantity: 1,
    },
  }),
];

validateBaseUrl();

if (IS_PRODUCTION) {
  throw new Error('Mixed workload is blocked on production. Use the isolated develop environment.');
}

if (!['smoke', 'baseline', 'capacity', 'spike', 'breakpoint'].includes(normalizedProfile)) {
  throw new Error(
    `Unknown PROFILE '${PROFILE}'. Use smoke, baseline, capacity, spike, breakpoint, or stress.`,
  );
}

export const options = {
  discardResponseBodies: false,
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
  userAgent: 'seonjalal-k6-mixed-read/1.0',
  ...profileOptions(normalizedProfile),
};

export function mixedRead() {
  execute(weightedChoice(weightedRequests));
}

export function smokeAll() {
  for (const definition of weightedRequests) {
    execute(definition);
  }
}

function execute(definition) {
  const tags = { endpoint: definition.endpoint, workload: 'mixed_read' };
  const params = {
    headers: {
      Accept: 'application/json',
      Authorization: `Bearer ${accessTokenForVu()}`,
    },
    tags,
    timeout: '5s',
  };

  let response;
  if (definition.method === 'POST') {
    params.headers['Content-Type'] = 'application/json';
    response = http.post(
      `${BASE_URL}${definition.path}`,
      JSON.stringify(definition.body),
      params,
    );
  } else {
    response = http.get(`${BASE_URL}${definition.path}`, params);
  }

  let valid = false;
  if (response.status === 200) {
    try {
      const body = response.json();
      valid = body?.error == null && definition.validate(body?.data ?? null);
    } catch (_) {
      valid = false;
    }
  }

  businessErrors.add(!valid, tags);
  check(
    response,
    {
      [`${definition.endpoint}: status is 200`]: (result) => result.status === 200,
      [`${definition.endpoint}: response contract is valid`]: () => valid,
    },
    tags,
  );
}

function profileOptions(profile) {
  const thresholds = {
    'checks{workload:mixed_read}': [
      'rate>0.995',
      {
        threshold: 'rate>0.95',
        abortOnFail: true,
        delayAbortEval: '30s',
      },
    ],
    'http_req_failed{workload:mixed_read}': [
      'rate<0.005',
      {
        threshold: 'rate<0.05',
        abortOnFail: true,
        delayAbortEval: '30s',
      },
    ],
    'business_errors{workload:mixed_read}': ['rate<0.005'],
    'http_req_duration{workload:mixed_read}': ['p(95)<500', 'p(99)<1000'],
    dropped_iterations: ['count==0'],
  };

  if (profile === 'smoke') {
    return {
      scenarios: {
        mixed_read_smoke: {
          executor: 'shared-iterations',
          exec: 'smokeAll',
          vus: 1,
          iterations: 1,
          maxDuration: '1m',
          tags: { workload: 'mixed_read', test_type: 'smoke' },
        },
      },
      thresholds: {
        'checks{workload:mixed_read}': ['rate==1'],
        'http_req_failed{workload:mixed_read}': ['rate==0'],
        'business_errors{workload:mixed_read}': ['rate==0'],
      },
    };
  }

  if (profile === 'baseline') {
    return {
      scenarios: {
        mixed_read: arrivalScenario('constant-arrival-rate', {
          rate: 5,
          duration: '5m',
          preAllocatedVUs: 20,
          maxVUs: 50,
        }),
      },
      thresholds,
    };
  }

  if (profile === 'capacity') {
    return {
      scenarios: {
        mixed_read: arrivalScenario('ramping-arrival-rate', {
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
      },
      thresholds,
    };
  }

  if (profile === 'spike') {
    return {
      scenarios: {
        mixed_read: arrivalScenario('ramping-arrival-rate', {
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
      },
      thresholds,
    };
  }

  return {
    scenarios: {
      mixed_read: arrivalScenario('ramping-arrival-rate', {
        startRate: 30,
        preAllocatedVUs: 500,
        maxVUs: 1000,
        stages: [
          { duration: '3m', target: 30 },
          { duration: '3m', target: 60 },
          { duration: '3m', target: 100 },
          { duration: '3m', target: 200 },
          { duration: '3m', target: 300 },
          { duration: '3m', target: 500 },
          { duration: '1m', target: 0 },
        ],
      }),
    },
    thresholds,
  };
}

function arrivalScenario(executor, configuration) {
  return {
    executor,
    exec: 'mixedRead',
    timeUnit: '1s',
    tags: { workload: 'mixed_read', test_type: normalizedProfile },
    ...configuration,
  };
}

function request(endpoint, weight, path, validate, extras = {}) {
  return { endpoint, weight, path, validate, method: 'GET', ...extras };
}

function weightedChoice(definitions) {
  let position = Math.random() * 100;
  for (const definition of definitions) {
    position -= definition.weight;
    if (position < 0) {
      return definition;
    }
  }
  return definitions[definitions.length - 1];
}

function normalizeProfile(profile) {
  if (profile === 'load') return 'capacity';
  if (profile === 'stress') return 'breakpoint';
  return profile;
}

function categories(data) {
  return Array.isArray(data?.categories);
}

function products(data) {
  return Array.isArray(data?.products);
}

function productDetail(data) {
  return data?.product?.productId === PRODUCT_ID;
}

function userProfile(data) {
  return Number.isInteger(data?.userId) && data.userId > 0;
}

function friends(data) {
  return Array.isArray(data?.friends);
}

function giftList(data) {
  return Array.isArray(data?.items);
}

function giftDetail(data) {
  return Number.isInteger(data?.giftId) && data.giftId > 0;
}

function objectData(data) {
  return data !== null && typeof data === 'object';
}
