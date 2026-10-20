#!/usr/bin/env python3
"""Mac: V2 시험 원장과 RDS를 읽기 전용 대조. 비밀값 출력·DB 쓰기 없음."""
import argparse
import importlib.util
import json
import re
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', required=True)
    parser.add_argument('--local-port', type=int, default=13306)
    args = parser.parse_args()
    if not 1 <= args.local_port <= 65535:
        raise SystemExit('STOP: 포트 범위 오류')
    spec = importlib.util.spec_from_file_location(
        'candidate_db', Path(__file__).with_name('prepare-v1-candidate-db.py'))
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    runtime = helper.runtime
    directory = runtime.protected_directory(args.directory)
    # 이번에 실제 실행된 두 Run만 대조. 다른 환경·임의 SQL 실행 방지.
    root = Path(__file__).resolve().parent.parent / 'load-tests/results'
    runs = ('20261018t221400z-v2-write-smoke',
            '20261018t221435z-v2-write-baseline')
    expected_gifts, expected_users = {}, {}
    for run, count in zip(runs, (1, 30)):
        rows = []
        for line in (root / run / 'request.log').read_text().splitlines():
            if '{' not in line:
                continue
            row = json.loads(line[line.index('{'):])
            if row.get('kind') == 'infra_comparison_request':
                runtime.require(row.get('target') == 'v2' and row.get('runId') == run
                                and row.get('valid') is True, '요청 원장 검토 필요')
                rows.append(row)
        gifts = [r for r in rows if r['endpoint'] == 'gift_create']
        users = [r for r in rows if r['endpoint'] == 'signup']
        runtime.require(len(gifts) == count and len(users) == (1 if count == 1 else 10),
                        '원장 요청 수 불일치')
        for row in gifts:
            runtime.require(re.fullmatch(r'[0-9a-fA-F-]{36}', row['requestId']) is not None,
                            '원장 멱등키 형식 오류')
            key = row['requestId'].replace('-', '').upper()
            expected_gifts[row['giftId']] = (
                row['giftId'], row['senderId'], row['recipientUserId'], row['productId'],
                row['quantity'], row['expectedUnitPrice'], 'COMPLETED', key)
        for row in users:
            expected_users[row['createdUserId']] = run
    runtime.require(len(expected_gifts) == 31 and len(expected_users) == 11,
                    '시험 ID 중복 확인 필요')
    runtime.require(len({row[-1] for row in expected_gifts.values()}) == 31,
                    '멱등키 중복 확인 필요')
    master = helper.management_credentials()
    with runtime.connect_db(directory, args.local_port, master['username'], master['password']) as conn:
        with conn.cursor() as cursor:
            cursor.execute('START TRANSACTION READ ONLY')
            try:
                for user_id, run in expected_users.items():
                    cursor.execute(f'SELECT status,email,deleted_at FROM `{runtime.DB_NAME}`.users WHERE id=%s', (user_id,))
                    user = cursor.fetchone()
                    runtime.require(user and user[0] == 'ACTIVE' and user[2] is None
                                    and user[1].startswith('loadtest' + run + '.')
                                    and user[1].endswith('@seonjalal.invalid'), '시험 사용자 불일치')
                for gift_id, expected in expected_gifts.items():
                    cursor.execute(f'SELECT id,sender_id,recipient_id,product_id,quantity,product_price_snapshot,status,HEX(idempotency_key) FROM `{runtime.DB_NAME}`.gift_histories WHERE id=%s', (gift_id,))
                    runtime.require(tuple(cursor.fetchone() or ()) == expected, '선물 원장 불일치')
                cursor.execute(f'SELECT quantity FROM `{runtime.DB_NAME}`.products WHERE id=4')
                runtime.require(cursor.fetchone() == (56,), '상품4 재고 기대값56 불일치')
                cursor.execute(f'SELECT COUNT(*),MAX(id) FROM `{runtime.DB_NAME}`.gift_histories')
                runtime.require(cursor.fetchone() == (5053, 5053), '전체 선물 기준 불일치: 동시 쓰기 여부 검토 필요')
            finally:
                cursor.execute('ROLLBACK')
    print('V2_ACTIVE_TEST_USERS_VERIFIED=11')
    print('V2_GIFT_ROWS_AND_IDEMPOTENCY_KEYS_VERIFIED=31')
    print('PRODUCT_4_STOCK=56')
    print('TOTAL_GIFTS=5053')
    print('V2_WRITE_DB_VERIFICATION_PASSED_NO_DB_WRITE')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        raise SystemExit('STOP: DB 대조 실패. 상세 원문 비공개. TYPE=' + type(exc).__name__)
