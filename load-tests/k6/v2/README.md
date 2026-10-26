# V2 스테이징 부하 시험

BE 2.0.0에서 새로 생긴 API를 `https://v2-staging.seonjalal.com`에 시험한다. AI 기능은 제외한다.

## 시나리오

### 읽기 전용 (단일 엔드포인트와 혼합)

| 파일 | 호출 | 성공 조건 |
|---|---|---|
| `credits-balance.js` | `GET /api/credits/balance` | `data.balance` 정수 |
| `preferences.js` | `GET /api/preferences` | `data`에 `preference` 키(null 가능) |
| `dislike-categories.js` | `GET /api/preferences/dislike-categories` | `maxSelectableCount` 5, `categories` 배열 |
| `products-search.js` | `GET /api/products?sort=POPULAR&query=<키워드>` | `products` 배열, `appliedSort` 문자열 |
| `notifications-list.js` | `GET /api/notifications` | `items` 배열, `pagination` |
| `notifications-unread.js` | `GET /api/notifications/unread-count` | `unreadCount` 정수 |
| `friend-requests-received.js` | `GET /api/friend-requests/received` | `items` 배열, `pagination` |
| `friend-requests-sent.js` | `GET /api/friend-requests/sent` | `items` 배열, `pagination` |
| `../workloads/v2-read-mixed.js` | 위 8개를 가중치로 혼합 | 각 항목의 조건 |

### 읽기 80% : 쓰기 20% 혼합 (`../workloads/v2-mixed-rw.js`)

한 번의 반복(iteration)이 아래 작업 중 하나를 가중치로 고른다. 쓰기는 서로 다른 사용자가 이어지는 **흐름**으로 만들어 DB에 데이터를 미리 심지 않아도 반복된다. 서명 비밀로 어떤 사용자의 토큰도 만들 수 있으므로 친구 요청·선물을 받는 사람의 토큰도 같은 시험 안에서 만든다.

| 구분 | 작업(반복 비중) | 요청 구성 |
|---|---|---|
| 읽기 86% | 상품 검색 14, 목록 10, 상세 12, 내 정보 6, 친구 목록 6, 받은 선물 목록 8, 크레딧 8, 알림 목록 6, 미읽음 5, 설정 3, 싫어하는 카테고리 2, 받은 요청 3, 보낸 요청 3 | GET 1 |
| 쓰기 2% | 설정 저장 | PUT 1 |
| 쓰기 2% | 싫어하는 카테고리 저장 | PUT 1 |
| 쓰기 2% | 리뷰 흐름 | GET(받은 선물) → POST 리뷰 → DELETE 리뷰 |
| 쓰기 3% | 친구 요청 흐름 | POST 요청 → 받는 사람이 POST 수락(양쪽 +100,000 크레딧) |
| 쓰기 4% | **결제 → 수락** | GET 친구 목록 → POST 결제(Main → Payment 서버) → GET 결제 상태 → 받는 사람이 POST 선물 수락 |
| 쓰기 1% | **결제 → 취소** | GET 친구 목록 → POST 결제 → POST 취소(환불, 재고 복구) |

- 요청 단위 쓰기 비율은 설계값 약 19.8%(읽기 97 : 쓰기 24)이고, 로컬 모의 서버에서 6,000회 반복으로 측정한 값은 19.3%다.
- 결제는 시나리오 헤더(`X-Payment-Scenario`) 없이 기본 승인 경로를 쓴다(결제 서버의 4초 지연 시나리오는 사용하지 않는다).
- 결제 상품과 가격은 시험 시작 시(`setup`) 검색으로 한 번 모은다(`expectedUnitPrice`가 현재 가격과 달라지면 409).
- **스모크에서는 쓰기도 정상 성공만 합격**이다(409, 422 같은 정상 업무 오류를 허용하지 않는다). 본 시험에서는 정상 업무 오류(이미 친구, 재고 부족, 크레딧 부족 등)를 실패로 세지 않고 `v2_expected_business_errors`에 따로 센다.
- 요청마다 `rw`(`read`/`write`) 태그가 붙어 결과를 읽기와 쓰기로 나눠 분석할 수 있다.
- 합격 기준: 읽기 p95 500ms / p99 1초, 쓰기 p95 800ms / p99 1.5초(V1 잠정 SLO 기준), 실패율 0.5% 미만, 누락(dropped) 0.
- 데이터가 계속 쌓이고 크레딧과 재고가 움직인다. 스테이징 전용이다. 매일 03:00 KST의 "수락되지 않은 선물 일괄 취소" 작업과 시간이 겹치지 않게 한다.
- 알림과 친구 요청은 읽기 전용 시험에서는 빈 목록이었지만, 이 혼합 시험을 돌리면 쓰기 흐름이 알림과 친구 요청을 만들기 때문에 점점 데이터가 있는 상태가 된다.

## 시험 대상 사용자

시험 대상은 새로 추가한 **9,500명(`loadtest00501`~`loadtest10000`, 5자리 번호)**이다. 기존 500명은 `loadtest001`~`500`(3자리)이라 형식이 다르고 ID를 확인하지 않아 제외한다.

사용자 ID는 번호와 연속이고(2026-10-26 확인: 501번 → 1048, 10000번 → 10547), 시나리오의 사용자 번호는 1부터 9,500까지 순환하며 번호 1이 `loadtest00501`에 해당한다. 따라서 `V2_USER_ID_OFFSET = (501번의 ID) - 1`, `V2_USER_COUNT = 9500`이다. 값은 `verify-user-ids.js`가 출력한다(현재 스테이징: 1047).

## 안전 장치

| 장치 | 내용 |
|---|---|
| 대상 제한 | `BASE_URL`이 `https://v2-staging.seonjalal.com`이 아니면 시작하지 않는다. 운영(`www`)과 다른 호스트는 차단 |
| 옵트인 | `ALLOW_DIRECT_JWT_STAGING=true`가 있어야 서명 비밀로 토큰을 만든다 |
| 상한 RPS | 스테이징 ALB는 **운영과 공용**이다. `spike`(60), `breakpoint`(최대 500)는 `ALLOW_ABOVE_30_RPS=true` 없이는 시작하지 않는다 |
| 중단 기준 | 실패율 5% 초과, 체크 성공률 95% 미만이 30초 이상 지속되면 자동 중단 |
| 비밀 비출력 | 서명 비밀, 게이트 토큰, 토큰은 출력하지 않는다. 환경변수로만 받는다 |

## 환경변수

| 이름 | 내용 | 비밀 |
|---|---|---|
| `BASE_URL` | `https://v2-staging.seonjalal.com` | 아니오 |
| `ALLOW_DIRECT_JWT_STAGING` | `true` | 아니오 |
| `STAGING_GATE_TOKEN` | 접근 게이트 토큰(`stg_gate` 쿠키로 전송) | **예** |
| `STAGING_JWT_SECRET_BASE64` | 스테이징 JWT 서명 비밀(Secrets Manager `seonjalal/staging/backend/split-runtime`의 `JWT_SECRET_BASE64`) | **예** |
| `STAGING_JWT_ISSUER` | JWT 발급자(ECS 작업 정의의 `JWT_ISSUER`, 비밀 아님) | 아니오 |
| `V2_USER_ID_OFFSET` | 사용자 ID = 이 값 + 번호(번호 1이 `loadtest00501`). 현재 스테이징 1047 | 아니오 |
| `V2_USER_COUNT` | 시험 대상 사용자 수 9500 | 아니오 |
| `LOADTEST_PASSWORD` | 픽스처 계정 비밀번호(`verify-user-ids.js`만 사용) | **예** |
| `PROFILE` | `smoke`, `baseline`, `capacity` | 아니오 |

비밀은 명령행에 직접 쓰지 않는다. `read -rs`(입력이 보이지 않음)나 명령 치환으로 환경변수에만 넣고, 시험이 끝나면 `unset`으로 지운다.

## 로컬 모의 서버 검증

실제 서버에 보내기 전에 코드를 검증한다. AWS에는 연결하지 않고 가짜 비밀만 쓴다.

```
python3 -B -m unittest scripts.test_k6_v2_scenarios
```

가드(운영 차단, 옵트인, 상한 RPS), 게이트 쿠키와 JWT 서명 검증, 실패 감지(403, 401, 500은 임계값 실패로 종료), 비밀 비출력, 쓰기 흐름(수락은 받는 사람의 토큰만 통과), 읽기:쓰기 비율, 스모크의 엄격 모드를 확인한다. 모의 서버는 `mock-server.mjs`이고 `V2_ALLOW_LOCAL_MOCK=true`일 때만 `http://127.0.0.1:<포트>`를 허용한다. `PROFILE=mockcheck`(반복 횟수 지정)는 모의 서버에서만 쓸 수 있다.

## 실행 순서

1. 스테이징이 켜져 있는지 확인한다(`python3 -B scripts/staging-power.py status`).
2. 위 환경변수를 한 터미널 창에 설정한다.
3. `verify-user-ids.js`로 사용자 ID 연속 여부를 확인하고 `V2_USER_ID_OFFSET`을 정한다(로그인 3번).
4. 스모크: 시나리오별 → 혼합 스모크(`PROFILE=smoke`)
5. 읽기 혼합 `baseline`(5 RPS, 5분) → 읽기 80 : 쓰기 20 혼합 스모크 → `baseline` → 필요하면 `capacity`(최대 30 반복/초)
6. 시험 중 Grafana에서 스테이징 ECS, RDS, ALB와 운영 ALB 대상 그룹의 5xx, 응답 시간을 함께 본다.
7. 끝나면 환경변수를 지운다.

## 한계

- SSE(`/notifications/events`)는 k6로 스트림을 받을 수 없어 제외한다.
- 결제 서버의 지연·실패 시나리오(`X-Payment-Scenario`)는 스테이징 결제 서버 프로파일(local/test/dev)을 확인한 뒤에만 쓸 수 있어 포함하지 않았다.
- 부하는 Mac에서 인터넷을 거쳐 CloudFront로 간다. Mac이나 회선이 먼저 포화될 수 있다.
- 반복 한 번의 요청 수가 작업마다 다르다(1~4). "반복/초"와 "요청/초"가 같지 않다(혼합에서 요청은 반복의 약 1.2배).
