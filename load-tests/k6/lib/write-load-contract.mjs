// 부하 없이 검사할 수 있는 V1/V2 쓰기 시험 계약. 운영 URL·무제한 쓰기는 허용하지 않는다.
export const TARGETS = {
  v1: 'https://v1-benchmark.seonjalal.com',
  v2: 'https://seonjalal-v2-develop-runtime.tail234485.ts.net:8443',
};

function requireValue(condition, message) {
  if (!condition) throw new Error(message);
}

export function validatePlan(env, fixture) {
  requireValue(env.ALLOW_COMPARISON_WRITES === '1', 'Explicit test-write approval required');
  requireValue(['v1', 'v2'].includes(env.LOAD_TARGET), 'Unknown test target');
  requireValue(env.BASE_URL === TARGETS[env.LOAD_TARGET], 'Exact non-production URL required');
  requireValue(['smoke', 'baseline'].includes(env.PROFILE), 'Only smoke or five-minute baseline allowed');
  requireValue(/^[a-z0-9-]{8,48}$/.test(env.LOAD_RUN_ID || ''), 'Unique lowercase run ID required');
  requireValue(fixture.target === env.LOAD_TARGET, 'Fixture belongs to another environment');
  requireValue(fixture.friendshipsVerified === true && fixture.stockVerified === true
    && fixture.sideEffectsReviewed === true, 'Read-only fixture/side-effect preflight required');
  requireValue(Array.isArray(fixture.actors) && fixture.actors.length > 0, 'Test actors required');
  for (const actor of fixture.actors) {
    requireValue(/^loadtest[a-z0-9.-]*@seonjalal\.invalid$/.test(actor.email || ''), 'Dedicated test email required');
    requireValue(typeof actor.password === 'string' && actor.password.length >= 8
      && !actor.password.includes('REPLACE'), 'Real local test password required');
    requireValue(typeof actor.accessToken === 'string' && actor.accessToken.split('.').length === 3
      && !actor.accessToken.includes('REPLACE'), 'Real login-issued gift token required');
    requireValue(Number.isSafeInteger(actor.userId) && actor.userId > 0
      && Number.isSafeInteger(actor.recipientUserId) && actor.recipientUserId > 0
      && actor.userId !== actor.recipientUserId, 'Distinct sender/recipient required');
  }
  requireValue(Array.isArray(fixture.products) && fixture.products.length > 0, 'Test products required');
  for (const product of fixture.products) {
    requireValue(Number.isSafeInteger(product.productId) && product.productId > 0
      && Number.isSafeInteger(product.expectedUnitPrice) && product.expectedUnitPrice >= 0,
    'Product ID and exact unit price required');
  }
  requireValue(typeof fixture.signupPassword === 'string'
    && fixture.signupPassword.length >= 8 && fixture.signupPassword.length <= 64
    && /[A-Za-z]/.test(fixture.signupPassword) && /[0-9]/.test(fixture.signupPassword)
    && /[!@#$%^&*()_+\-=]/.test(fixture.signupPassword)
    && !fixture.signupPassword.includes('REPLACE'), 'Valid local signup password required');
  const minimum = env.PROFILE === 'smoke' ? { signups: 1, gifts: 1 } : { signups: 10, gifts: 30 };
  requireValue(Number.isSafeInteger(fixture.maxSignups) && fixture.maxSignups >= minimum.signups
    && fixture.maxSignups <= 100, 'Signup attempt budget insufficient or excessive');
  requireValue(Number.isSafeInteger(fixture.maxGiftAttempts) && fixture.maxGiftAttempts >= minimum.gifts
    && fixture.maxGiftAttempts <= 100, 'Gift attempt budget insufficient or excessive');
  return { target: env.LOAD_TARGET, baseUrl: env.BASE_URL, profile: env.PROFILE,
    runId: env.LOAD_RUN_ID, maxSignups: fixture.maxSignups, maxGiftAttempts: fixture.maxGiftAttempts };
}

export function termConsents(response) {
  const terms = response && response.data && response.data.terms;
  requireValue(Array.isArray(terms) && terms.length > 0, 'Current signup terms missing');
  requireValue(terms.some(term => term.isRequired === true), 'Required signup terms missing');
  const ids = new Set();
  return terms.map(term => {
    requireValue(Number.isSafeInteger(term.termId) && term.termId > 0
      && Number.isSafeInteger(term.version) && term.version > 0 && !ids.has(term.termId),
    'Invalid or duplicate signup term');
    ids.add(term.termId);
    return { termId: term.termId, version: term.version, isAgreed: true };
  });
}

export function signupEmail(runId, iteration) {
  return `loadtest${runId}.${iteration}@seonjalal.invalid`;
}

// 1건 smoke의 분위수를 성능 합격 기준으로 사용하지 않는다. 원래 duration은 계속 기록한다.
export function performanceThresholds(profile) {
  requireValue(['smoke', 'baseline'].includes(profile), 'Unsupported threshold profile');
  return profile === 'baseline'
    ? { 'http_req_duration{endpoint:products_list}': ['p(95)<500', 'p(99)<1000'] }
    : {};
}

export function requestLimits(profile) {
  requireValue(['smoke', 'baseline'].includes(profile), 'Unsupported request profile');
  return profile === 'smoke'
    ? { products_list: 1, login: 1, signup: 1, gift_create: 1 }
    : { products_list: 1500, login: 30, signup: 10, gift_create: 30 };
}

export function canAttempt(iteration, limit) {
  requireValue(Number.isSafeInteger(iteration) && iteration >= 0
    && Number.isSafeInteger(limit) && limit > 0, 'Invalid attempt index/budget');
  return iteration < limit;
}

export function completionThresholds(profile) {
  const result = {};
  for (const [endpoint, count] of Object.entries(requestLimits(profile))) {
    result[`comparison_request_completions{endpoint:${endpoint}}`] = [`count==${count}`];
  }
  return result;
}
