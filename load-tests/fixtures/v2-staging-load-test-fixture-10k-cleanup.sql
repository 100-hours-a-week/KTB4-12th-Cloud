-- 선잘알 V2 Staging 부하테스트 Fixture(10,000명) 정리
-- 목적: v2-staging-load-test-fixture-10k.sql이 추가한 501~10000번 사용자와 그 관계 데이터만 삭제한다.
-- 대상 스키마: Flyway V12. V13 이후에는 크레딧·알림 등 사용자를 참조하는 Table이 늘어나므로 이 파일로 정리하지 않는다.
-- V13 이후의 초기화는 Fixture 적재 직전에 만든 DB 스냅샷 복원을 기준으로 한다.
-- 기존 Fixture 500명(loadtest001~500)과 Seed 계정은 삭제하지 않는다.
-- 안전 경계: Production에서는 실행하지 않는다. 사용자를 참조하는 다른 Table이 있으면 FK 오류로 전체가 롤백된다.

CREATE TEMPORARY TABLE load_test_extra_users (
    user_id BIGINT NOT NULL PRIMARY KEY
);

-- 번호 501 이상의 Fixture 사용자만 대상으로 한다. 이메일 형식이 맞지 않는 계정은 건드리지 않는다.
INSERT INTO load_test_extra_users (user_id)
SELECT id
FROM users
WHERE email REGEXP '^loadtest[0-9]+@seonjalal\\.invalid$'
  AND CAST(SUBSTRING_INDEX(SUBSTRING_INDEX(email, '@', 1), 'loadtest', -1) AS UNSIGNED) > 500;

SELECT COUNT(*) AS users_to_delete FROM load_test_extra_users;

START TRANSACTION;

-- 신규 사용자가 보낸·받은 선물과 맺은 친구 관계를 먼저 지운다. 기존 500명이 신규 사용자와 맺은 행도 함께 지워진다.
-- MySQL은 같은 임시 테이블을 한 문장에서 두 번 열 수 없어 칼럼별로 문장을 나눈다.
DELETE FROM gift_histories
WHERE sender_id IN (SELECT user_id FROM load_test_extra_users);

DELETE FROM gift_histories
WHERE recipient_id IN (SELECT user_id FROM load_test_extra_users);

DELETE FROM friends
WHERE user_id IN (SELECT user_id FROM load_test_extra_users);

DELETE FROM friends
WHERE friend_user_id IN (SELECT user_id FROM load_test_extra_users);

DELETE FROM term_consents
WHERE user_id IN (SELECT user_id FROM load_test_extra_users);

DELETE FROM users
WHERE id IN (SELECT user_id FROM load_test_extra_users);

COMMIT;

-- 정리 후에는 Fixture 사용자 500명만 남아야 한다.
SELECT COUNT(*) AS remaining_load_test_users
FROM users
WHERE email REGEXP '^loadtest[0-9]+@seonjalal\\.invalid$';
