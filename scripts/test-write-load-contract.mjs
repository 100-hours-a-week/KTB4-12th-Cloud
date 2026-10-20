import assert from 'node:assert/strict';
import { test } from 'node:test';
import { validatePlan, termConsents, signupEmail, performanceThresholds,
  requestLimits, canAttempt, completionThresholds } from '../load-tests/k6/lib/write-load-contract.mjs';

const env = { LOAD_TARGET: 'v1', BASE_URL: 'https://v1-benchmark.seonjalal.com',
  PROFILE: 'baseline', LOAD_RUN_ID: 'synthetic-run-001', ALLOW_COMPARISON_WRITES: '1' };
function fixture() {
  return { target: 'v1', friendshipsVerified: true, stockVerified: true, sideEffectsReviewed: true,
    actors: [{ email: 'loadtest001@seonjalal.invalid', password: 'FakeOnly123!',
      accessToken: 'fake.token.signature', userId: 1, recipientUserId: 2 }],
    products: [{ productId: 4, expectedUnitPrice: 22900 }], signupPassword: 'FakeSignup123!',
    maxSignups: 10, maxGiftAttempts: 30 };
}

test('valid bounded baseline', () => assert.equal(validatePlan(env, fixture()).maxGiftAttempts, 30));
test('production URL blocked', () => assert.throws(() => validatePlan({ ...env, BASE_URL: 'https://seonjalal.com' }, fixture())));
test('environment mix blocked', () => assert.throws(() => validatePlan({ ...env, LOAD_TARGET: 'v2' }, fixture())));
test('explicit approval required', () => assert.throws(() => validatePlan({ ...env, ALLOW_COMPARISON_WRITES: '0' }, fixture())));
test('stress not silently allowed', () => assert.throws(() => validatePlan({ ...env, PROFILE: 'stress' }, fixture())));
test('insufficient gift budget blocked', () => { const f = fixture(); f.maxGiftAttempts = 29; assert.throws(() => validatePlan(env, f)); });
test('insufficient signup budget blocked', () => { const f = fixture(); f.maxSignups = 9; assert.throws(() => validatePlan(env, f)); });
test('fixture preflight required', () => { const f = fixture(); f.stockVerified = false; assert.throws(() => validatePlan(env, f)); });
test('real customer account blocked', () => { const f = fixture(); f.actors[0].email = 'person@example.com'; assert.throws(() => validatePlan(env, f)); });
test('self gift blocked', () => { const f = fixture(); f.actors[0].recipientUserId = 1; assert.throws(() => validatePlan(env, f)); });
test('unique run iteration emails', () => assert.notEqual(signupEmail('synthetic-run', 1), signupEmail('synthetic-run', 2)));
test('last allowed gift iteration is 29, index 30 is safely skipped', () => {
  assert.equal(canAttempt(29, 30), true);
  assert.equal(canAttempt(30, 30), false);
});
test('last signup iteration and exact boundary', () => {
  assert.equal(canAttempt(9, 10), true);
  assert.equal(canAttempt(10, 10), false);
});
test('negative or invalid iteration cannot bypass budget', () => {
  assert.throws(() => canAttempt(-1, 30));
  assert.throws(() => canAttempt(NaN, 30));
});
test('fixed baseline counts cannot be masked by successful skipped iterations', () => {
  assert.equal(requestLimits('baseline').gift_create, 30);
  assert.deepEqual(completionThresholds('baseline')['comparison_request_completions{endpoint:gift_create}'], ['count==30']);
});
test('single request smoke has no percentile gate', () => assert.deepEqual(performanceThresholds('smoke'), {}));
test('five minute baseline keeps original performance gate', () => assert.deepEqual(performanceThresholds('baseline'), {
  'http_req_duration{endpoint:products_list}': ['p(95)<500', 'p(99)<1000'],
}));
test('current terms contract', () => assert.deepEqual(termConsents({ data: { terms: [
  { termId: 1, version: 2, isRequired: true },
] } }), [{ termId: 1, version: 2, isAgreed: true }]));
test('missing or duplicate terms rejected', () => {
  assert.throws(() => termConsents({ data: { terms: [] } }));
  assert.throws(() => termConsents({ data: { terms: [
    { termId: 1, version: 1, isRequired: true }, { termId: 1, version: 1, isRequired: true },
  ] } }));
});
