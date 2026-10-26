import http from 'k6/http';
import { check } from 'k6';
import exec from 'k6/execution';

import { BASE_URL } from '../lib/config.js';
import { assertAllowedTarget, gateHeaders } from '../lib/v2.js';

// 시험 대상은 새로 추가한 9,500명(loadtest00501~10000)이다. 기존 500명은 loadtest001~500(3자리)이라 형식이 다르고 ID도 확인하지 않았다.
// 이 번호들의 DB 사용자 ID가 연속인지 로그인 3번(501, 5000, 10000)으로 확인한다. 로그인은 IP당 분당 10회 제한이라 3번만 보낸다.
// 토큰과 비밀번호는 출력하지 않는다. 로그인하면 각 계정에 일일 출석 보상(+100,000 크레딧)이 한 번 들어간다.
const PASSWORD = String(__ENV.LOADTEST_PASSWORD || '');
const NUMBERS = [501, 5000, 10000];
const FIRST = NUMBERS[0];
const MIDDLE = NUMBERS[1];
const LAST = NUMBERS[2];

// 가드는 초기화 단계에서 검사한다. 반복 안에서 던진 예외는 k6가 실행을 중단하지 않고 exit 0으로 끝낸다.
assertAllowedTarget();
if (PASSWORD === '') {
  throw new Error('LOADTEST_PASSWORD is required. Set it with read -rs, never on the command line.');
}

export const options = {
  vus: 1,
  iterations: 1,
  thresholds: { checks: ['rate==1'] },
};

export default function () {
  const ids = {};
  for (const number of NUMBERS) {
    const email = `loadtest${String(number).padStart(5, '0')}@seonjalal.invalid`;
    const response = http.post(
      `${BASE_URL}/api/auth/login`,
      JSON.stringify({ email, password: PASSWORD }),
      {
        headers: { 'Content-Type': 'application/json', Accept: 'application/json', ...gateHeaders() },
        timeout: '10s',
      },
    );
    const userId = response.status === 200 ? response.json('data.user.userId') : null;
    check(response, { [`login ${number}: status 200 with userId`]: () => Number.isInteger(userId) });
    ids[number] = userId;
  }

  const contiguous =
    ids[MIDDLE] - ids[FIRST] === MIDDLE - FIRST && ids[LAST] - ids[MIDDLE] === LAST - MIDDLE;
  // 시나리오의 사용자 번호는 1부터 9,500까지이고 번호 1이 loadtest00501에 해당한다. ID = 오프셋 + 번호.
  const offset = ids[FIRST] - 1;
  console.log(
    JSON.stringify({
      userIds: ids,
      contiguous,
      V2_USER_ID_OFFSET: contiguous ? offset : null,
      V2_USER_COUNT: contiguous ? LAST - FIRST + 1 : null,
    }),
  );
  if (!contiguous) {
    // 반복만 중단하는 fail()이 아니라 실행 전체를 비정상 종료한다.
    exec.test.abort('User ids are not contiguous with the loadtest number. Export the id list from the database instead.');
  }
}
