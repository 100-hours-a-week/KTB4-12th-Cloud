import {
  IS_PRODUCTION,
  PROFILE,
  requireAccessToken,
  validateBaseUrl,
} from './config.js';

const SUPPORTED_PROFILES = [
  'smoke',
  'baseline',
  'capacity',
  'spike',
  'breakpoint',
  'stress',
];

export function apiTestOptions({
  endpoint,
  exec,
  authenticated = true,
  changesData = false,
}) {
  validateBaseUrl();

  if (authenticated) {
    requireAccessToken();
  }

  const normalizedProfile = normalizeProfile(PROFILE);
  if (!SUPPORTED_PROFILES.includes(normalizedProfile)) {
    throw new Error(
      `Unknown PROFILE '${PROFILE}'. Use ${SUPPORTED_PROFILES.join(', ')}.`,
    );
  }

  guardProduction(normalizedProfile, changesData);

  return {
    discardResponseBodies: false,
    summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
    userAgent: 'seonjalal-k6-api/2.0',
    ...profileOptions(normalizedProfile, endpoint, exec),
  };
}

function profileOptions(profile, endpoint, exec) {
  if (profile === 'smoke') {
    return {
      scenarios: {
        [endpoint]: {
          executor: 'shared-iterations',
          exec,
          vus: 1,
          iterations: 3,
          maxDuration: '30s',
          tags: { test_type: 'smoke' },
        },
      },
      thresholds: smokeThresholds(endpoint),
    };
  }

  if (profile === 'baseline') {
    return {
      scenarios: {
        [endpoint]: {
          executor: 'constant-arrival-rate',
          exec,
          rate: 5,
          timeUnit: '1s',
          duration: '5m',
          preAllocatedVUs: 20,
          maxVUs: 50,
          tags: { test_type: 'baseline' },
        },
      },
      thresholds: performanceThresholds(endpoint),
    };
  }

  if (profile === 'capacity') {
    return {
      scenarios: {
        [endpoint]: {
          executor: 'ramping-arrival-rate',
          exec,
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
          tags: { test_type: 'capacity' },
        },
      },
      thresholds: performanceThresholds(endpoint),
    };
  }

  if (profile === 'spike') {
    return {
      scenarios: {
        [endpoint]: {
          executor: 'ramping-arrival-rate',
          exec,
          startRate: 5,
          timeUnit: '1s',
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
          tags: { test_type: 'spike' },
        },
      },
      thresholds: performanceThresholds(endpoint),
    };
  }

  return {
    scenarios: {
      [endpoint]: {
        executor: 'ramping-arrival-rate',
        exec,
        startRate: 30,
        timeUnit: '1s',
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
        tags: { test_type: 'breakpoint' },
      },
    },
    thresholds: performanceThresholds(endpoint),
  };
}

function normalizeProfile(profile) {
  if (profile === 'load') {
    return 'capacity';
  }
  if (profile === 'stress') {
    return 'breakpoint';
  }
  return profile;
}

function smokeThresholds(endpoint) {
  return {
    [`checks{endpoint:${endpoint}}`]: ['rate==1'],
    [`http_req_failed{endpoint:${endpoint}}`]: ['rate==0'],
    [`business_errors{endpoint:${endpoint}}`]: ['rate==0'],
  };
}

function performanceThresholds(endpoint) {
  return {
    [`checks{endpoint:${endpoint}}`]: [
      'rate>0.995',
      {
        threshold: 'rate>0.95',
        abortOnFail: true,
        delayAbortEval: '30s',
      },
    ],
    [`http_req_failed{endpoint:${endpoint}}`]: [
      'rate<0.005',
      {
        threshold: 'rate<0.05',
        abortOnFail: true,
        delayAbortEval: '30s',
      },
    ],
    [`http_req_duration{endpoint:${endpoint}}`]: [
      'p(95)<500',
      'p(99)<1000',
    ],
    [`business_errors{endpoint:${endpoint}}`]: ['rate<0.005'],
    dropped_iterations: ['count==0'],
  };
}

function guardProduction(profile, changesData) {
  if (!IS_PRODUCTION) {
    return;
  }

  if (changesData) {
    throw new Error(
      'This scenario changes database state and is blocked on production. Use an isolated develop environment.',
    );
  }

  if (
    profile === 'capacity' ||
    profile === 'spike' ||
    profile === 'breakpoint'
  ) {
    throw new Error(
      `${profile} tests are blocked on production. Use an isolated develop environment.`,
    );
  }

  if (profile === 'baseline' && __ENV.ALLOW_PRODUCTION !== 'true') {
    throw new Error(
      'Production baseline test blocked. Set ALLOW_PRODUCTION=true only after the run checklist is approved.',
    );
  }
}
