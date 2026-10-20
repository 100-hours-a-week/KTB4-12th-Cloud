-- 선잘알 V1 Develop 부하테스트 전용 Fixture
-- 목적: Test1~Test500 사용자와 친구·받은 선물 분포를 항상 같은 규칙으로 생성한다.
-- 안전 경계: Production에서는 실행하지 않는다. 실행 전 A-pre-fixture 백업이 반드시 있어야 한다.
-- 재실행 정책: 같은 loadtest 이메일이 하나라도 존재하면 Guard가 실패해 중복 생성을 막는다.

-- MySQL은 같은 임시 테이블을 한 Query에서 여러 번 열 수 없으므로 자릿수별 테이블을 분리한다.
-- 세 테이블의 숫자 0~9를 조합해 1~500과 관계 Slot 1~100을 만든다.
CREATE TEMPORARY TABLE load_test_hundreds_digits (
    digit TINYINT NOT NULL PRIMARY KEY
);

CREATE TEMPORARY TABLE load_test_tens_digits (
    digit TINYINT NOT NULL PRIMARY KEY
);

CREATE TEMPORARY TABLE load_test_ones_digits (
    digit TINYINT NOT NULL PRIMARY KEY
);

INSERT INTO load_test_hundreds_digits (digit)
VALUES (0), (1), (2), (3), (4), (5), (6), (7), (8), (9);

INSERT INTO load_test_tens_digits (digit)
VALUES (0), (1), (2), (3), (4), (5), (6), (7), (8), (9);

INSERT INTO load_test_ones_digits (digit)
VALUES (0), (1), (2), (3), (4), (5), (6), (7), (8), (9);

CREATE TEMPORARY TABLE load_test_numbers AS
SELECT number_value AS n
FROM (
    SELECT hundreds.digit * 100 + tens.digit * 10 + ones.digit + 1 AS number_value
    FROM load_test_hundreds_digits AS hundreds
    CROSS JOIN load_test_tens_digits AS tens
    CROSS JOIN load_test_ones_digits AS ones
) AS generated_numbers
WHERE number_value BETWEEN 1 AND 500;

ALTER TABLE load_test_numbers ADD PRIMARY KEY (n);

-- 이미 Fixture 사용자가 있으면 CHECK 제약조건을 의도적으로 실패시킨다.
-- 부분 생성 상태에서 덮어쓰지 않고 A-pre-fixture 복원 후 다시 실행하기 위한 보호 장치다.
CREATE TEMPORARY TABLE load_test_existing_user_guard (
    existing_count INT NOT NULL,
    CONSTRAINT chk_no_existing_load_test_users CHECK (existing_count = 0)
);

INSERT INTO load_test_existing_user_guard (existing_count)
SELECT COUNT(*)
FROM users
WHERE email LIKE 'loadtest%@seonjalal.invalid';

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

-- MySQL은 같은 임시 테이블을 한 Query에서 여러 Alias로 열 수 없다.
-- 친구 상대와 선물 발신자 조회용으로 같은 사용자 매핑을 별도 임시 테이블에 복제한다.
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

-- 모든 Test 계정은 Develop Seed 계정과 같은 bcrypt Test 비밀번호를 사용한다.
-- 실제 평문 비밀번호는 Backend Seed 정책의 Test1234!이며 Production 자격 증명과 무관하다.
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
    CONCAT('loadtest', LPAD(n, 3, '0'), '@seonjalal.invalid'),
    '$2a$10$90EeqOYdmT5Vz4OYhJav0.9tc83uIdAaC1U8ncVare0Ax7ynF9Gaq',
    CONCAT('Test', n),
    DATE_ADD('1985-01-01', INTERVAL MOD(n * 37, 7300) DAY),
    'ACTIVE',
    IF(MOD(n, 2) = 0, b'1', b'0'),
    b'0',
    NULL,
    UTC_TIMESTAMP(6),
    UTC_TIMESTAMP(6)
FROM load_test_numbers;

-- Auto Increment ID를 가정하지 않고 이메일에서 Fixture 번호를 복원해 실제 User ID와 연결한다.
INSERT INTO load_test_users (n, user_id)
SELECT
    CAST(SUBSTRING_INDEX(SUBSTRING_INDEX(email, '@', 1), 'loadtest', -1) AS UNSIGNED) AS n,
    id AS user_id
FROM users
WHERE email LIKE 'loadtest%@seonjalal.invalid';

INSERT INTO load_test_counterpart_users (n, user_id)
SELECT n, user_id
FROM load_test_users;

-- 최신 버전의 모든 약관에 동의시켜 로그인 후 사용자 Journey가 약관 화면에서 멈추지 않게 한다.
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
 );

-- Persona별 목표 친구 수를 만든다.
-- Light(Test1~250): 1~3명, Normal(Test251~450): 5~15명, Heavy(Test451~500): 30~100명.
-- 37과 17을 사용한 Modular 규칙은 고정 Seed처럼 동작해 실행할 때마다 같은 관계를 만든다.
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
JOIN load_test_numbers AS slot
  ON slot.n <= CASE
      WHEN owner_user.n <= 250 THEN 1 + MOD(owner_user.n * 37, 3)
      WHEN owner_user.n <= 450 THEN 5 + MOD(owner_user.n * 37, 11)
      ELSE 30 + MOD(owner_user.n * 37, 71)
  END
JOIN load_test_counterpart_users AS friend_user
  ON friend_user.n = CASE
      WHEN MOD(owner_user.n * 37 + slot.n * 17 - 1, 500) + 1 = owner_user.n
          THEN MOD(owner_user.n, 500) + 1
      ELSE MOD(owner_user.n * 37 + slot.n * 17 - 1, 500) + 1
  END;

-- 모든 선물은 받은 선물 API에 노출되도록 COMPLETED 상태로 만든다.
-- Light: 1~2개, Normal: 3~10개, Heavy: 30~100개를 생성한다.
-- MD5 결과의 16 Byte 값은 UUID 저장 칼럼용 결정적 Idempotency Key로만 사용하며 보안 용도가 아니다.
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
    UNHEX(MD5(CONCAT('v1-load-fixture:', recipient_user.n, ':', slot.n))),
    SHA2(CONCAT('v1-load-fixture:', recipient_user.n, ':', slot.n), 256),
    TIMESTAMPADD(HOUR, -MOD(recipient_user.n * 31 + slot.n * 17, 2160), UTC_TIMESTAMP(6)),
    NULL,
    TIMESTAMPADD(HOUR, -MOD(recipient_user.n * 31 + slot.n * 17, 2160), UTC_TIMESTAMP(6)),
    UTC_TIMESTAMP(6)
FROM load_test_users AS recipient_user
JOIN load_test_numbers AS slot
  ON slot.n <= CASE
      WHEN recipient_user.n <= 250 THEN 1 + MOD(recipient_user.n * 37, 2)
      WHEN recipient_user.n <= 450 THEN 3 + MOD(recipient_user.n * 37, 8)
      ELSE 30 + MOD(recipient_user.n * 37, 71)
  END
JOIN load_test_counterpart_users AS sender_user
  ON sender_user.n = MOD(recipient_user.n + slot.n * 13 - 1, 500) + 1
JOIN load_test_products AS product
  ON product.product_sequence = MOD(
      recipient_user.n * 31 + slot.n * 17 - 1,
      @load_test_product_count
  ) + 1;

COMMIT;

-- 실행 직후 기대 분포를 확인한다. 사용자는 500명이고 모든 사용자는 친구·받은 선물이 있어야 한다.
SELECT
    COUNT(*) AS load_test_users,
    MIN(user_id) AS minimum_user_id,
    MAX(user_id) AS maximum_user_id
FROM load_test_users;

SELECT
    CASE
        WHEN load_user.n <= 250 THEN 'Light'
        WHEN load_user.n <= 450 THEN 'Normal'
        ELSE 'Heavy'
    END AS persona,
    COUNT(*) AS users,
    MIN(friend_count) AS minimum_friends,
    MAX(friend_count) AS maximum_friends,
    ROUND(AVG(friend_count), 2) AS average_friends
FROM load_test_users AS load_user
JOIN (
    SELECT user_id, COUNT(*) AS friend_count
    FROM friends
    WHERE deleted_at IS NULL
    GROUP BY user_id
) AS friend_summary
  ON friend_summary.user_id = load_user.user_id
GROUP BY persona
ORDER BY FIELD(persona, 'Light', 'Normal', 'Heavy');

SELECT
    CASE
        WHEN load_user.n <= 250 THEN 'Light'
        WHEN load_user.n <= 450 THEN 'Normal'
        ELSE 'Heavy'
    END AS persona,
    COUNT(*) AS users,
    MIN(received_gift_count) AS minimum_received_gifts,
    MAX(received_gift_count) AS maximum_received_gifts,
    ROUND(AVG(received_gift_count), 2) AS average_received_gifts
FROM load_test_users AS load_user
JOIN (
    SELECT recipient_id, COUNT(*) AS received_gift_count
    FROM gift_histories
    WHERE status = 'COMPLETED'
      AND deleted_at IS NULL
    GROUP BY recipient_id
) AS gift_summary
  ON gift_summary.recipient_id = load_user.user_id
GROUP BY persona
ORDER BY FIELD(persona, 'Light', 'Normal', 'Heavy');
