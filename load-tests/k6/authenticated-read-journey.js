import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend } from 'k6/metrics';

const PROFILE = __ENV.PROFILE || 'smoke';
const BASE_URL = (__ENV.BASE_URL || '').replace(/\/$/, '');
const ACCESS_TOKEN = __ENV.ACCESS_TOKEN || '';
const REQUIRE_GIFT_DETAILS = __ENV.REQUIRE_GIFT_DETAILS === 'true';
const IS_PRODUCTION = /^https?:\/\/(www\.)?seonjalal\.com(?::\d+)?(?:\/|$)/i.test(BASE_URL);

const businessErrors = new Rate('business_errors');
const journeySuccess = new Rate('journey_success');
const journeyDuration = new Trend('journey_duration', true);
const receivedGiftDetailCoverage = new Rate('received_gift_detail_coverage');

const strictCoverageThresholds = REQUIRE_GIFT_DETAILS
  ? {
      'received_gift_detail_coverage{journey:authenticated_read}': ['rate==1'],
    }
  : {};

const profiles = {
  smoke: {
    scenarios: {
      authenticated_read_journey: {
        executor: 'per-vu-iterations',
        exec: 'authenticatedReadJourney',
        vus: 1,
        iterations: 1,
        maxDuration: '1m',
        tags: { journey: 'authenticated_read' },
      },
    },
    thresholds: {
      'journey_success{journey:authenticated_read}': ['rate==1'],
      'http_req_failed{journey:authenticated_read}': ['rate==0'],
      'business_errors{journey:authenticated_read}': ['rate==0'],
      ...strictCoverageThresholds,
    },
  },
  baseline: {
    scenarios: {
      authenticated_read_journey: {
        executor: 'constant-vus',
        exec: 'authenticatedReadJourney',
        vus: 3,
        duration: '5m',
        gracefulStop: '30s',
        tags: { journey: 'authenticated_read' },
      },
    },
    thresholds: {
      'journey_success{journey:authenticated_read}': ['rate>0.99'],
      'http_req_failed{journey:authenticated_read}': ['rate<0.01'],
      'business_errors{journey:authenticated_read}': ['rate<0.01'],
      'http_req_duration{journey:authenticated_read}': [
        'p(95)<500',
        'p(99)<1000',
      ],
      ...strictCoverageThresholds,
    },
  },
};

if (!BASE_URL || !/^https?:\/\//i.test(BASE_URL)) {
  throw new Error('BASE_URL must be an http(s) URL. Example: https://dev.example.com');
}

if (!ACCESS_TOKEN) {
  throw new Error('ACCESS_TOKEN is required. Do not store the token in the repository.');
}

if (!profiles[PROFILE]) {
  throw new Error(`Unknown PROFILE '${PROFILE}'. Use smoke or baseline.`);
}

if (IS_PRODUCTION && PROFILE !== 'smoke' && __ENV.ALLOW_PRODUCTION !== 'true') {
  throw new Error(
    'Production baseline test blocked. Set ALLOW_PRODUCTION=true after completing the run checklist.',
  );
}

export const options = {
  discardResponseBodies: false,
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
  userAgent: 'seonjalal-k6-authenticated-read/1.0',
  ...profiles[PROFILE],
};

const requestParams = {
  headers: {
    Authorization: `Bearer ${ACCESS_TOKEN}`,
  },
  timeout: '5s',
};

export function authenticatedReadJourney() {
  const startedAt = Date.now();
  let succeeded = true;

  const categories = getAndValidate(
    '/api/products/categories',
    'product_categories',
    (data) => Array.isArray(data?.categories),
  );
  succeeded = categories.valid && succeeded;

  think(0.5, 1.5);

  const productList = getAndValidate(
    '/api/products?sort=POPULAR',
    'products_list',
    (data) => Array.isArray(data?.products),
  );
  succeeded = productList.valid && succeeded;

  const products = productList.data?.products || [];
  if (products.length > 0) {
    const product = selectItem(products);

    think(1, 3);

    const detail = getAndValidate(
      `/api/products/${product.productId}`,
      'product_detail',
      (data) => data?.product?.productId === product.productId,
    );
    succeeded = detail.valid && succeeded;
  } else {
    succeeded = false;
  }

  think(1, 3);

  const receivedGifts = getAndValidate(
    '/api/gifts/received',
    'received_gifts',
    (data) => Array.isArray(data?.items),
  );
  succeeded = receivedGifts.valid && succeeded;

  const receivedItems = receivedGifts.data?.items || [];
  receivedGiftDetailCoverage.add(receivedItems.length > 0, {
    journey: 'authenticated_read',
  });
  if (receivedItems.length > 0) {
    const gift = selectItem(receivedItems);
    const receivedDetail = getAndValidate(
      `/api/gifts/received/${gift.giftId}`,
      'received_gift_detail',
      (data) => data?.giftId === gift.giftId,
    );
    succeeded = receivedDetail.valid && succeeded;
  } else if (REQUIRE_GIFT_DETAILS) {
    succeeded = false;
  }

  journeySuccess.add(succeeded, { journey: 'authenticated_read' });
  journeyDuration.add(Date.now() - startedAt, { journey: 'authenticated_read' });
}

function getAndValidate(path, endpoint, validateData) {
  const tags = { endpoint, journey: 'authenticated_read' };
  const response = http.get(`${BASE_URL}${path}`, {
    ...requestParams,
    tags,
  });

  let data = null;
  let valid = false;

  if (response.status === 200) {
    try {
      const body = response.json();
      data = body?.data ?? null;
      valid = body?.error == null && validateData(data);
    } catch (_) {
      valid = false;
    }
  }

  businessErrors.add(!valid, tags);

  check(
    response,
    {
      [`${endpoint}: status is 200`]: (res) => res.status === 200,
      [`${endpoint}: response contract is valid`]: () => valid,
    },
    tags,
  );

  return { data, valid };
}

function selectItem(items) {
  return items[(__VU + __ITER) % items.length];
}

function think(minSeconds, maxSeconds) {
  sleep(minSeconds + Math.random() * (maxSeconds - minSeconds));
}
