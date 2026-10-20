import fs from 'node:fs';
import { createHash } from 'node:crypto';

// k6가 JSON 문자열로 이스케이프한 원장만 읽는다. 토큰/응답 본문은 출력하지 않는다.
const logPath = process.argv[2];
if (!logPath) throw new Error('Usage: node scripts/summarize-migration-log.mjs LOG_PATH');
const records = [];
let malformed = 0;
for (const line of fs.readFileSync(logPath, 'utf8').split('\n')) {
  if (!line.includes('migration_request')) continue;
  try {
    const match = line.match(/\bmsg=("(?:\\.|[^"\\])*")/);
    if (!match) throw new Error('Missing JSON log envelope');
    const record = JSON.parse(JSON.parse(match[1]));
    if (record.kind === 'migration_request') records.push(record);
  } catch {
    malformed++;
  }
}
const gifts = records.filter((row) => row.endpoint === 'gift_create');
const successfulGifts = gifts.filter((row) => row.valid === true && row.status === 201);
const uncertain = records.filter((row) => row.valid !== true);
const ids = successfulGifts.map((row) => row.giftId);
const requestKeys = successfulGifts.map((row) => row.requestId);
// 이번 COMPLETED 선물 흐름의 기대값. MySQL의 정렬된 GROUP_CONCAT 결과와 비교한다.
// 비교 대상 필드를 한정한 원장 대조이며 모든 컬럼/관계의 검증으로 확대하지 않는다.
const expectedRows = successfulGifts.slice().sort((a, b) => a.giftId - b.giftId)
  .map((row) => [
    row.giftId, 524, row.recipientUserId, row.productId, row.quantity,
    row.expectedUnitPrice, 'COMPLETED',
    String(row.requestId).replaceAll('-', '').toUpperCase(),
  ].join('|'));
const counters = {};
for (const row of records) counters[row.endpoint] = (counters[row.endpoint] || 0) + 1;
console.log(JSON.stringify({
  records: records.length,
  malformed_records: malformed,
  counts_by_endpoint: counters,
  unsuccessful_or_uncertain_requests: uncertain.length,
  successful_gifts: successfulGifts.length,
  duplicate_gift_ids: ids.length - new Set(ids).size,
  duplicate_request_ids: requestKeys.length - new Set(requestKeys).size,
  run_ids: [...new Set(records.map((row) => row.runId))],
  first_request: records.map((row) => row.startedAt).sort()[0] ?? null,
  last_response: records.map((row) => row.finishedAt).sort().at(-1) ?? null,
  gift_ids_csv: ids.slice().sort((a, b) => a - b).join(','),
  expected_gift_ledger_sha256: createHash('sha256').update(expectedRows.join(',')).digest('hex'),
}, null, 2));

// 기록 누락·미확정 응답을 성공으로 세지 않으며 API 재전송도 하지 않는다.
if (!records.length || malformed || uncertain.length
    || ids.some((id) => !Number.isSafeInteger(id) || id <= 0)
    || requestKeys.some((key) => typeof key !== 'string')) process.exitCode = 1;
