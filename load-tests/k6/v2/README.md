# V2 스테이징 부하 시험 (읽기 API)

BE 2.0.0에서 새로 생긴 읽기 API를 `https://v2-staging.seonjalal.com`에 시험한다. AI 기능은 제외한다.

## 시나리오

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
| `../workloads/v2-read-mixed.js` | 위 8개를 가중치로 혼합(검색 25, 크레딧 20, 알림 목록 15 등) | 각 항목의 조건 |

- 알림과 친구 요청은 픽스처에 데이터가 없어 **빈 목록이 정상 응답**이다. 이 시험은 "빈 응답을 빠르게 주는가"만 확인한다. 데이터가 있는 상태의 성능은 쓰기 시나리오 이후에 다시 본다.
- 검색 결과가 비는 비율은 `v2_search_empty` 지표로 기록한다. 알려진 키워드인데 계속 비면 ngram 검색이나 검색 엔진 설정을 의심한다.

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
| `V2_USER_ID_OFFSET` | 사용자 ID = 이 값 + 번호(`loadtest00001`의 번호는 1). `verify-user-ids.js`로 확인 | 아니오 |
| `V2_USER_COUNT` | 사용자 수, 기본 10000 | 아니오 |
| `LOADTEST_PASSWORD` | 픽스처 계정 비밀번호(`verify-user-ids.js`만 사용) | **예** |
| `PROFILE` | `smoke`, `baseline`, `capacity` | 아니오 |

비밀은 명령행에 직접 쓰지 않는다. `read -rs`(입력이 보이지 않음)나 명령 치환으로 환경변수에만 넣고, 시험이 끝나면 `unset`으로 지운다.

## 로컬 모의 서버 검증

실제 서버에 보내기 전에 코드를 검증한다. AWS에는 연결하지 않고 가짜 비밀만 쓴다.

```
python3 -B -m unittest scripts.test_k6_v2_scenarios
```

가드(운영 차단, 옵트인, 상한 RPS), 게이트 쿠키와 JWT 서명 검증, 실패 감지(403, 401, 500은 임계값 실패로 종료), 비밀 비출력을 확인한다. 모의 서버는 `mock-server.mjs`이고 `V2_ALLOW_LOCAL_MOCK=true`일 때만 `http://127.0.0.1:<포트>`를 허용한다.

## 실행 순서

1. 스테이징이 켜져 있는지 확인한다(`python3 -B scripts/staging-power.py status`).
2. 위 환경변수를 한 터미널 창에 설정한다.
3. `verify-user-ids.js`로 사용자 ID 연속 여부를 확인하고 `V2_USER_ID_OFFSET`을 정한다(로그인 3번).
4. 시나리오별 스모크(`PROFILE=smoke`, 3회)
5. 혼합 스모크 → 혼합 `baseline`(5 RPS, 5분) → 필요하면 `capacity`(최대 30 RPS)
6. 시험 중 Grafana에서 스테이징 ECS, RDS, ALB와 운영 ALB 대상 그룹의 5xx, 응답 시간을 함께 본다.
7. 끝나면 환경변수를 지운다.

## 한계

- 읽기 API만 포함한다. 결제, 선물 수락, 리뷰, 친구 요청 생성 같은 쓰기는 포함하지 않는다.
- `GET /products` 검색은 스테이징의 검색 설정(MySQL ngram 또는 Elasticsearch)을 따른다. 설정값은 코드로 확정할 수 없다.
- SSE(`/notifications/events`)는 k6로 스트림을 받을 수 없어 제외한다.
- 부하는 Mac에서 인터넷을 거쳐 CloudFront로 간다. Mac이나 회선이 먼저 포화될 수 있다.
