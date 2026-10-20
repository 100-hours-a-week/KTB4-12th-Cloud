import assert from 'node:assert/strict';
import { classifyCutoverRead as read, classifyCutoverGift as gift }
  from '../load-tests/k6/lib/migration-cutover-contract.js';

// HTTP 호출 없이 응답 분류만 검증한다. 실제 가용성/복제 검증의 대체가 아니다.
const cases = [
  [read(200, { data: { products: [] } }), 'read_success'],
  [read(200, { data: {} }), 'read_failure'],
  [read(200, { error: { code: 'ERROR' }, data: { products: [] } }), 'read_failure'],
  [read(503, { error: { code: 'MIGRATION_WRITE_FROZEN' } }), 'read_failure'],
  [read(0, null), 'read_failure'],
  [gift(201, { data: { gift: { giftId: 1 } } }), 'gift_success'],
  [gift(201, { data: { gift: { giftId: 0 } } }), 'write_failure'],
  [gift(201, { error: { code: 'ERROR' }, data: { gift: { giftId: 1 } } }), 'write_failure'],
  [gift(503, { error: { code: 'MIGRATION_WRITE_FROZEN' } }), 'write_frozen'],
  [gift(503, null), 'write_failure'],
  [gift(503, { error: { code: 'OTHER' } }), 'write_failure'],
  [gift(502, null), 'write_failure'],
  [gift(401, null), 'write_failure'],
  [gift(0, null), 'write_failure'],
];
for (const [actual, expected] of cases) assert.equal(actual, expected);
console.log(`CUTOVER_CONTRACT_TESTS_PASSED=${cases.length}`);
