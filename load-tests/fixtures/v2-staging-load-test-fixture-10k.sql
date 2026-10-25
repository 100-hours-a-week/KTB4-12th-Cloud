-- 선잘알 V2 Staging 부하테스트 전용 Fixture (사용자 10,000명)
-- 목적: 기존 Fixture 500명(loadtest001~500)은 그대로 두고 501~10000번을 추가해 총 10,000명으로 확장한다.
-- 대상 스키마: Flyway V12 (V13 이후 마이그레이션 전에 실행). V13 이후에는 신규 Table·NOT NULL 칼럼이 생기므로 이 파일을 그대로 쓰지 않는다.
-- 안전 경계: Production에서는 실행하지 않는다. 실행 전에 Staging DB 수동 스냅샷이 반드시 있어야 한다.
-- 재실행 정책: 기준(기존 500명이 정확히 있고 501번 이상이 없음)이 아니면 Guard가 실패해 중복 생성을 막는다.
-- 재현성: 난수를 쓰지 않고 모듈러 규칙만 사용하므로 실행할 때마다 같은 관계가 만들어진다.

SET SESSION cte_max_recursion_depth = 20000;
SET @load_test_total_users = 10000;
SET @load_test_base_users = 500;

-- 사용자 번호 1~총원, 관계 슬롯 1~100을 만드는 임시 테이블.
-- MySQL은 같은 임시 테이블을 한 Query에서 여러 번 열 수 없어 용도별로 분리한다.
CREATE TEMPORARY TABLE load_test_numbers (
    n INT NOT NULL PRIMARY KEY
);

INSERT INTO load_test_numbers (n)
WITH RECURSIVE sequence_numbers (n) AS (
    SELECT 1
    UNION ALL
    SELECT n + 1 FROM sequence_numbers WHERE n < @load_test_total_users
)
SELECT n FROM sequence_numbers;

CREATE TEMPORARY TABLE load_test_slots (
    n INT NOT NULL PRIMARY KEY
);

INSERT INTO load_test_slots (n)
SELECT n FROM load_test_numbers WHERE n <= 100;

-- 기준 Guard: 기존 Fixture 500명(번호 1~500)이 정확히 있고 501번 이상은 없어야 실행한다.
-- 기존 500명이 없으면 친구·선물 상대를 연결할 수 없고, 501번 이상이 있으면 부분 생성 상태이므로 복원 후 다시 실행한다.
CREATE TEMPORARY TABLE load_test_existing_user_guard (
    base_count INT NOT NULL,
    extra_count INT NOT NULL,
    CONSTRAINT chk_load_test_base_exact CHECK (base_count = 500 AND extra_count = 0)
);

INSERT INTO load_test_existing_user_guard (base_count, extra_count)
SELECT
    COALESCE(SUM(fixture_number BETWEEN 1 AND 500), 0),
    COALESCE(SUM(fixture_number > 500), 0)
FROM (
    SELECT CAST(SUBSTRING_INDEX(SUBSTRING_INDEX(email, '@', 1), 'loadtest', -1) AS UNSIGNED) AS fixture_number
    FROM users
    WHERE email REGEXP '^loadtest[0-9]+@seonjalal\\.invalid$'
) AS existing_fixture_users;

-- 사용할 수 있는 활성 상품이 없으면 선물 Fixture를 만들 수 없으므로 실행을 중단한다.
CREATE TEMPORARY TABLE load_test_product_guard (
    product_count INT NOT NULL,
    CONSTRAINT chk_active_products_exist CHECK (product_count > 0)
);

INSERT INTO load_test_product_guard (product_count)
SELECT COUNT(*)
FROM products
WHERE deleted_at IS NULL;

-- Application Table 변경 후 DDL이 암시적 COMMIT을 만들지 않도록 필요한 임시 테이블을 미리 정의한다.
CREATE TEMPORARY TABLE load_test_users (
    n INT NOT NULL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    CONSTRAINT uk_load_test_user_id UNIQUE (user_id)
);

-- 친구 상대·선물 발신자 조회용 복제본. 같은 임시 테이블을 한 Query에서 여러 Alias로 열 수 없다.
CREATE TEMPORARY TABLE load_test_counterpart_users (
    n INT NOT NULL PRIMARY KEY,
    user_id BIGINT NOT NULL,
    CONSTRAINT uk_load_test_counterpart_user_id UNIQUE (user_id)
);

-- 삭제되지 않은 상품에 1부터 순번을 부여해 실제 Product ID의 중간 공백과 무관하게 선택한다.
CREATE TEMPORARY TABLE load_test_products AS
SELECT
    ROW_NUMBER() OVER (ORDER BY id) AS product_sequence,
    id AS product_id,
    name AS product_name,
    price AS product_price
FROM products
WHERE deleted_at IS NULL;

SET @load_test_product_count = (
    SELECT COUNT(*)
    FROM load_test_products
);

-- 아래 Application Table 변경은 모두 성공해야만 COMMIT한다.
START TRANSACTION;

-- 501~10000번 계정을 만든다. 비밀번호는 기존 Fixture와 같은 bcrypt 값이며 평문은 Backend Seed 정책의 Test1234!이다.
-- 이메일 번호는 5자리로 채운다(loadtest00501). 기존 3자리(loadtest001)와 번호 파싱 규칙이 같아 함께 조회된다.
INSERT INTO users (
    email,
    password,
    name,
    birth,
    status,
    is_birthday_public,
    is_first_login,
    deleted_at,
    created_at,
    updated_at
)
SELECT
    CONCAT('loadtest', LPAD(n, 5, '0'), '@seonjalal.invalid'),
    '$2a$10$90EeqOYdmT5Vz4OYhJav0.9tc83uIdAaC1U8ncVare0Ax7ynF9Gaq',
    CONCAT('Test', n),
    DATE_ADD('1985-01-01', INTERVAL MOD(n * 37, 7300) DAY),
    'ACTIVE',
    IF(MOD(n, 2) = 0, b'1', b'0'),
    b'0',
    NULL,
    UTC_TIMESTAMP(6),
    UTC_TIMESTAMP(6)
FROM load_test_numbers
WHERE n > @load_test_base_users;

-- Auto Increment ID를 가정하지 않고 이메일에서 번호를 복원해 기존 500명과 신규 사용자를 모두 실제 User ID에 연결한다.
INSERT INTO load_test_users (n, user_id)
SELECT
    CAST(SUBSTRING_INDEX(SUBSTRING_INDEX(email, '@', 1), 'loadtest', -1) AS UNSIGNED) AS n,
    id AS user_id
FROM users
WHERE email REGEXP '^loadtest[0-9]+@seonjalal\\.invalid$';

INSERT INTO load_test_counterpart_users (n, user_id)
SELECT n, user_id
FROM load_test_users;

-- 신규 사용자에게만 최신 버전의 모든 약관 동의를 만든다. 기존 500명은 이미 동의가 있다.
INSERT INTO term_consents (
    user_id,
    term_id,
    is_agreed,
    created_at,
    updated_at
)
SELECT
    load_user.user_id,
    term.id,
    b'1',
    UTC_TIMESTAMP(6),
    UTC_TIMESTAMP(6)
FROM load_test_users AS load_user
JOIN terms AS term
  ON term.deleted_at IS NULL
 AND term.version = (
     SELECT MAX(latest_term.version)
     FROM terms AS latest_term
     WHERE latest_term.term_code = term.term_code
       AND latest_term.deleted_at IS NULL
 )
WHERE load_user.n > @load_test_base_users;

-- 신규 사용자 Persona는 번호의 끝자리로 정해 기존 비율(Light 50%, Normal 40%, Heavy 10%)을 유지한다.
--   끝자리 0~4: Light(친구 1~3명), 5~8: Normal(5~15명), 9: Heavy(30~100명).
-- 친구 상대는 (내 번호 - 1 + 슬롯 * 7919) mod 총원 + 1이다. 7919는 총원과 서로소라 슬롯 100개까지
-- 서로 다른 상대가 나오고 자기 자신이 나오지 않는다. ID가 인접한 사용자끼리만 묶이는 편향도 피한다.
INSERT INTO friends (
    user_id,
    friend_user_id,
    deleted_at,
    created_at,
    updated_at
)
SELECT
    owner_user.user_id,
    friend_user.user_id,
    NULL,
    UTC_TIMESTAMP(6),
    UTC_TIMESTAMP(6)
FROM load_test_users AS owner_user
JOIN load_test_slots AS slot
  ON slot.n <= CASE
      WHEN MOD(owner_user.n, 10) <= 4 THEN 1 + MOD(owner_user.n * 37, 3)
      WHEN MOD(owner_user.n, 10) <= 8 THEN 5 + MOD(owner_user.n * 37, 11)
      ELSE 30 + MOD(owner_user.n * 37, 71)
  END
JOIN load_test_counterpart_users AS friend_user
  ON friend_user.n = MOD(owner_user.n - 1 + slot.n * 7919, @load_test_total_users) + 1
WHERE owner_user.n > @load_test_base_users;

-- 받은 선물: Light 1~2개, Normal 3~10개, Heavy 30~100개. 모두 COMPLETED 상태로 만든다.
-- 발신자는 (받는 사람 번호 - 1 + 슬롯 * 6007) mod 총원 + 1이다. 6007도 총원과 서로소다.
-- MD5 결과의 16 Byte 값은 UUID 저장 칼럼용 결정적 Idempotency Key로만 사용하며 보안 용도가 아니다.
-- 접두어를 v2-staging-fixture-10k로 구분해 기존 Fixture의 Key와 겹치지 않게 한다.
INSERT INTO gift_histories (
    sender_id,
    recipient_id,
    product_id,
    quantity,
    product_price_snapshot,
    product_name_snapshot,
    status,
    idempotency_key,
    request_fingerprint,
    completed_at,
    deleted_at,
    created_at,
    updated_at
)
SELECT
    sender_user.user_id,
    recipient_user.user_id,
    product.product_id,
    1 + MOD(slot.n, 3),
    product.product_price,
    product.product_name,
    'COMPLETED',
    UNHEX(MD5(CONCAT('v2-staging-fixture-10k:', recipient_user.n, ':', slot.n))),
    SHA2(CONCAT('v2-staging-fixture-10k:', recipient_user.n, ':', slot.n), 256),
    TIMESTAMPADD(HOUR, -MOD(recipient_user.n * 31 + slot.n * 17, 2160), UTC_TIMESTAMP(6)),
    NULL,
    TIMESTAMPADD(HOUR, -MOD(recipient_user.n * 31 + slot.n * 17, 2160), UTC_TIMESTAMP(6)),
    UTC_TIMESTAMP(6)
FROM load_test_users AS recipient_user
JOIN load_test_slots AS slot
  ON slot.n <= CASE
      WHEN MOD(recipient_user.n, 10) <= 4 THEN 1 + MOD(recipient_user.n * 37, 2)
      WHEN MOD(recipient_user.n, 10) <= 8 THEN 3 + MOD(recipient_user.n * 37, 8)
      ELSE 30 + MOD(recipient_user.n * 37, 71)
  END
JOIN load_test_counterpart_users AS sender_user
  ON sender_user.n = MOD(recipient_user.n - 1 + slot.n * 6007, @load_test_total_users) + 1
JOIN load_test_products AS product
  ON product.product_sequence = MOD(
      recipient_user.n * 31 + slot.n * 17 - 1,
      @load_test_product_count
  ) + 1
WHERE recipient_user.n > @load_test_base_users;

COMMIT;

-- 실행 직후 기대 분포를 확인한다.
-- 1) 총 사용자 10,000명(기존 500 + 신규 9,500)이어야 한다.
SELECT
    COUNT(*) AS load_test_users,
    SUM(n <= 500) AS existing_users,
    SUM(n > 500) AS new_users
FROM load_test_users;

-- 2) 신규 사용자 Persona별 친구 수. 모든 신규 사용자는 친구가 1명 이상이어야 한다.
SELECT
    CASE
        WHEN MOD(load_user.n, 10) <= 4 THEN 'Light'
        WHEN MOD(load_user.n, 10) <= 8 THEN 'Normal'
        ELSE 'Heavy'
    END AS persona,
    COUNT(*) AS users,
    MIN(COALESCE(friend_summary.friend_count, 0)) AS minimum_friends,
    MAX(COALESCE(friend_summary.friend_count, 0)) AS maximum_friends,
    ROUND(AVG(COALESCE(friend_summary.friend_count, 0)), 2) AS average_friends
FROM load_test_users AS load_user
LEFT JOIN (
    SELECT user_id, COUNT(*) AS friend_count
    FROM friends
    WHERE deleted_at IS NULL
    GROUP BY user_id
) AS friend_summary
  ON friend_summary.user_id = load_user.user_id
WHERE load_user.n > 500
GROUP BY persona
ORDER BY FIELD(persona, 'Light', 'Normal', 'Heavy');

-- 3) 신규 사용자 Persona별 받은 선물 수. 모든 신규 사용자는 받은 선물이 1개 이상이어야 한다.
SELECT
    CASE
        WHEN MOD(load_user.n, 10) <= 4 THEN 'Light'
        WHEN MOD(load_user.n, 10) <= 8 THEN 'Normal'
        ELSE 'Heavy'
    END AS persona,
    COUNT(*) AS users,
    MIN(COALESCE(gift_summary.received_gift_count, 0)) AS minimum_received_gifts,
    MAX(COALESCE(gift_summary.received_gift_count, 0)) AS maximum_received_gifts,
    ROUND(AVG(COALESCE(gift_summary.received_gift_count, 0)), 2) AS average_received_gifts
FROM load_test_users AS load_user
LEFT JOIN (
    SELECT recipient_id, COUNT(*) AS received_gift_count
    FROM gift_histories
    WHERE status = 'COMPLETED'
      AND deleted_at IS NULL
    GROUP BY recipient_id
) AS gift_summary
  ON gift_summary.recipient_id = load_user.user_id
WHERE load_user.n > 500
GROUP BY persona
ORDER BY FIELD(persona, 'Light', 'Normal', 'Heavy');

-- 4) 무결성: 같은 친구 쌍 중복과 자기 자신 친구가 없어야 한다(둘 다 0).
SELECT
    (SELECT COUNT(*) FROM (
        SELECT user_id, friend_user_id
        FROM friends
        GROUP BY user_id, friend_user_id
        HAVING COUNT(*) > 1
    ) AS duplicated_pairs) AS duplicated_friend_pairs,
    (SELECT COUNT(*) FROM friends WHERE user_id = friend_user_id) AS self_friends;
