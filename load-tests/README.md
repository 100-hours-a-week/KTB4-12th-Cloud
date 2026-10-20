# 선잘알 V1 부하 테스트 가이드

이 문서는 부하 테스트를 처음 수행하는 사람도 다음 세 가지를 구분할 수 있도록 작성한다.

1. 테스트 코드가 어떤 요청을 만드는가
2. k6 결과의 각 숫자가 무엇을 뜻하는가
3. 결과로 어디까지 판단할 수 있고, 무엇은 아직 판단할 수 없는가

현재 단계의 결론은 **운영 상품 목록 API가 계획한 최대 30 RPS 시나리오를 오류와
유실 없이 완료했다**는 것이다. 이것은 상품 목록 API의 배포 전 성능 증거이며, 서비스
전체의 최대 처리량이나 28일 운영 SLO 달성을 증명한 결과는 아니다.

## 1. 왜 k6를 선택했는가

선잘알 V1에서는 k6를 표준 부하 테스트 도구로 사용한다.

| 판단 기준 | k6 | JMeter | nGrinder | 선잘알 판단 |
|---|---|---|---|---|
| 테스트 작성 | JavaScript 코드 | GUI/JMX 중심 | Web UI와 Groovy/Jython | 코드 리뷰가 쉬운 k6 |
| Git 변경 이력 | 텍스트라 diff가 명확함 | JMX XML은 충돌·리뷰가 어려움 | Controller에서 관리 가능 | 저장소 중심 운영에 k6가 적합 |
| 일정 RPS 시험 | arrival-rate executor 제공 | Thread/Timer 조합 | VUser/Agent 방식 | 현재 5~30 RPS 표현에 k6가 단순 |
| 자동 합격 판정 | Threshold 내장 | Assertion·플러그인 구성 | 결과/Report UI 제공 | CI 실패 조건을 코드로 두기 쉬움 |
| 결과물 | CLI, JSON, HTML Dashboard, 외부 시계열 저장소 | JTL, HTML Dashboard | Web Report | k6만으로도 현재 요구 충족 |
| 분산 시험 | k6 Operator/Cloud 등 추가 구성 | Distributed mode | Controller-Agent가 핵심 장점 | V1 30 RPS에는 분산 구성이 불필요 |
| 현재 유지 상태 | 활발한 공식 문서·생태계 | 성숙한 Apache 프로젝트 | 공식 저장소가 2025-09-24에 archived | 신규 표준으로 nGrinder는 제외 |

JMeter가 나쁜 도구라는 뜻은 아니다. GUI로 복잡한 프로토콜 흐름을 조립하거나 기존
JMX 자산과 숙련자가 있다면 JMeter가 합리적이다. nGrinder도 다수 Agent를 Web UI에서
운영하고 결과를 관리하는 장점이 있다. 그러나 현재 시험은 HTTP API 하나에서 시작해
Git과 CI로 확장하므로, 실행 조건·검증·합격선을 한 파일에서 검토할 수 있는 k6가 가장
작은 운영 비용으로 목적을 충족한다.

도구 선택은 영구 결정이 아니다. 브라우저 기반 사용자 성능은 Playwright, 매우 높은
분산 HTTP 부하는 Gatling·Locust·k6 분산 실행처럼 목적에 맞는 도구를 별도로 검토한다.

## 2. 부하 테스트 코드를 어느 정도 알아야 하는가

모든 k6 API를 암기할 필요는 없다. 다만 테스트 결과에 책임을 지려면 아래 여섯 항목은
코드에서 직접 찾고 설명할 수 있어야 한다.

| 반드시 설명할 항목 | 현재 코드의 답 | 뜻 |
|---|---|---|
| 대상 | `GET /api/products?sort=POPULAR` | 어떤 사용자 기능을 호출하는가 |
| 부하 모델 | `shared-iterations`, `constant-arrival-rate`, `ramping-arrival-rate` | 요청을 어떤 속도로 발생시키는가 |
| 부하 크기 | 3회, 5 RPS, 최대 30 RPS | 서버에 얼마만큼 요청하는가 |
| 성공 조건 | HTTP 200 + `data.products` 배열 | 단순 응답뿐 아니라 정상 데이터 구조인지 확인 |
| 제한 시간 | 요청당 `5s` | 5초 안에 끝나지 않으면 요청 실패로 처리 |
| 합격선 | 오류율, p95, p99, dropped iteration | 테스트를 통과/실패로 나누는 기준 |

현재 요청 한 번의 흐름은 다음과 같다.

```text
k6가 상품 목록 GET 요청 전송
→ 최대 5초 대기
→ HTTP 200인지 검사
→ JSON의 data.products가 배열인지 검사
→ 응답시간·오류·검사 결과 기록
```

### VU, iteration, RPS의 차이

| 용어 | 의미 | 현재 테스트에서 읽는 법 |
|---|---|---|
| VU | 동시에 작업할 수 있는 가상 사용자 실행 슬롯 | 목표 RPS를 유지하는 데 필요할 때 k6가 사용 |
| iteration | 시나리오 함수 1회 실행 | 현재 함수에는 HTTP 요청이 하나이므로 1 iteration ≒ 1 request |
| RPS | 1초에 보낸 HTTP 요청 수 | 현재는 요청이 하나라 목표 iterations/s와 거의 같음 |

`60 preAllocatedVUs`는 사용자 60명이 항상 접속했다는 뜻이 아니다. k6가 목표 도착률을
놓치지 않도록 실행 슬롯 60개를 미리 준비했다는 뜻이다. 실제 완료 시험에서 동시에
필요했던 VU의 최댓값은 24였다.

## 3. 현재 시나리오

테스트 파일은 [`k6/products-list.js`](k6/products-list.js)이다.

| Profile | 부하 | 시간 | 목적 | 예상 요청 수 |
|---|---:|---:|---|---:|
| `smoke` | 1 VU로 총 3회 | 수 초, 최대 30초 | URL·라우팅·응답 구조가 맞는지 확인 | 3 |
| `baseline` | 5 RPS 고정 | 5분 | 낮은 부하의 기준 성능 측정 | 약 1,500 |
| `load` | 1→5→10→30 RPS, 30 RPS 20분 유지, 0으로 감소 | 32분 | V1 최대 가정인 30 RPS를 지속 처리하는지 확인 | 약 45,510 |

`load`의 시간별 목표는 다음과 같다.

| 구간 | 목표 변화 | 의미 |
|---|---|---|
| 0~2분 | 1 → 5 RPS | 작은 부하부터 안전하게 증가 |
| 2~5분 | 5 → 10 RPS | 평상시보다 높은 구간 확인 |
| 5~10분 | 10 → 30 RPS | 최대 목표까지 점진 증가 |
| 10~30분 | 30 RPS 유지 | 순간 성공이 아니라 지속 처리 확인 |
| 30~32분 | 30 → 0 RPS | 부하 감소 시 회복 확인 |

이것은 **단일 API의 open-model 도착률 시나리오**다. 실제 사용자 30명이 페이지를
둘러보는 E2E 시나리오가 아니며 로그인, 상세 조회, 선물 생성, 브라우저 렌더링도 포함하지
않는다.

## 4. 합격 기준과 각 수치의 의미

### 공통 기능 검사

| 수치 | 합격 기준 | 바로 읽는 의미 |
|---|---:|---|
| `status is 200` | 요청마다 참 | HTTP 요청을 정상 처리했는가 |
| `response has products array` | 요청마다 참 | 성공처럼 보이는 잘못된 JSON이 아니라 상품 배열을 돌려줬는가 |
| `business_errors` | Smoke 0%, 나머지 <1% | HTTP 연결은 성공했지만 응답 내용이 잘못된 비율 |

### Baseline·Load 합격선

| k6 지표 | 합격선 | 바로 읽는 의미 |
|---|---:|---|
| `checks` | 99% 초과 | 위 두 기능 검사 결과의 99%보다 많은 수가 성공해야 함 |
| `http_req_failed` | 1% 미만 | timeout, 연결 실패, 예상하지 않은 HTTP 응답 비율이 1% 미만이어야 함 |
| `http_req_duration p(95)` | 500ms 미만 | 가장 느린 5%를 제외한 요청이 0.5초 안에 끝나야 함 |
| `http_req_duration p(99)` | 1,000ms 미만 | 가장 느린 1%를 제외한 요청이 1초 안에 끝나야 함 |
| `dropped_iterations` | 0 | k6가 예정한 요청을 VU 부족 등으로 건너뛰면 안 됨 |

30초 이후 `checks`가 95% 이하이거나 HTTP 실패율이 5% 이상이면 안전을 위해 시험을
조기 중단하도록 설정했다. 99%·500ms·1초는 현재 **V1 잠정 목표**이며 사용자 연구와
운영 데이터로 확정된 장기 SLO는 아니다.

## 5. 지금까지의 측정 결과

### 5.1 Smoke — 실행 경로 검증 통과

| 측정값 | 결과 | 이 값이 뜻하는 것 |
|---|---:|---|
| 요청 | 3 | 용량 측정이 아니라 시험 코드가 정상 작동하는지 확인한 최소 요청 수 |
| checks | 6/6, 100% | 요청마다 2개 검사, 총 6개가 모두 성공 |
| HTTP 실패 | 0/3, 0% | 연결·timeout·HTTP 응답 실패 없음 |
| 비즈니스 오류 | 0/3, 0% | 3개 응답 모두 상품 배열 구조가 정상 |
| p95 | 65.6ms | 표본이 3개뿐이므로 성능 기준값으로 일반화하지 않음 |
| 최대 응답시간 | 68.78ms | 세 요청 중 가장 느린 한 요청의 시간 |

판정: **통과**. 운영 URL, Nginx 라우팅, Backend 응답과 검사 코드가 연결됐다.

### 5.2 Baseline — 5 RPS, 5분 통과

| 측정값 | 결과 | 이 값이 뜻하는 것 |
|---|---:|---|
| 요청/iteration | 1,501 | 약 5 RPS를 300초 동안 정상 실행한 횟수 |
| 실제 처리율 | 5.002803 req/s | 목표 5 RPS가 실제로 만들어짐 |
| checks | 3,002/3,002, 100% | 1,501개 요청의 두 기능 검사가 모두 성공 |
| HTTP 실패 | 0/1,501, 0% | 전송·응답 실패 없음 |
| 비즈니스 오류 | 0/1,501, 0% | 잘못된 상품 응답 없음 |
| 중앙값 | 32.4ms | 전체 요청의 절반은 32.4ms 이내에 완료 |
| p95 | 75.74ms | 요청 95%가 75.74ms 이내에 완료; 500ms 기준 통과 |
| p99 | 171.46ms | 요청 99%가 171.46ms 이내에 완료; 1초 기준 통과 |
| 최대 응답시간 | 656.75ms | 한 번의 느린 응답이 있었지만 p95·p99 분포는 안정적 |
| dropped iterations | 0 | 예정한 요청을 하나도 놓치지 않음 |
| 최대 사용 VU | 3 | 5 RPS를 유지하는 데 실제 동시 실행 슬롯은 최대 3개 필요 |

판정: **통과**. 낮은 부하에서 비교 기준으로 사용할 수 있는 응답시간을 확보했다.

### 5.3 Load 1차 — 중단되어 공식 판정에서 제외

| 측정값 | 결과 | 이 값이 뜻하는 것 |
|---|---:|---|
| 완료 요청 | 25,288 | 중단 전까지 처리한 요청 수 |
| HTTP 실패 | 11/25,288, 0.04% | timeout과 connection reset이 11회 발생 |
| failed checks | 22/50,576, 0.04% | 실패 요청마다 두 검사가 실패해 22건으로 집계 |
| dropped iterations | 6 | 목표 시점에 만들지 못한 iteration이 있어 해당 기준 실패 |
| p95 / p99 | 197.68ms / 625.39ms | 중단 전 표본에서는 잠정 지연 기준을 충족 |
| 최대 응답시간 | 16분 19초 | 정상 서버 처리시간으로 해석할 수 없는 실행 환경 이상 신호 |
| 종료 | `Ctrl+C`, 1 interrupted | 32분 시나리오를 끝내지 못함 |

판정: **무효 후 재시험**. 요청 실패가 관찰됐다는 사실은 보존하지만, Mac 절전·네트워크
정지 가능성과 수동 중단 때문에 서버의 최대 용량 실패로 단정하지 않는다.

### 5.4 Load 2차 — 최대 30 RPS 시나리오 통과

| 측정값 | 결과 | 이 값이 뜻하는 것 |
|---|---:|---|
| 요청/iteration | 45,510 | 32분 단계별 계획과 일치하는 전체 요청을 완료 |
| 평균 처리율 | 18.199937 req/s | ramp-up·30 RPS 유지·ramp-down 전체를 평균낸 값; 최대가 18 RPS라는 뜻이 아님 |
| checks | 91,020/91,020, 100% | 모든 요청의 HTTP 상태와 상품 배열 검사가 성공 |
| HTTP 실패 | 0/45,510, 0% | 연결·timeout·예상 응답 실패 없음 |
| 비즈니스 오류 | 0/45,510, 0% | 잘못된 상품 응답 없음 |
| 중앙값 | 30.02ms | 요청 절반이 30.02ms 이내에 완료 |
| p90 | 81.49ms | 요청 90%가 81.49ms 이내에 완료 |
| p95 | 133.7ms | 요청 95%가 133.7ms 이내에 완료; 500ms 기준 통과 |
| p99 | 319.97ms | 요청 99%가 319.97ms 이내에 완료; 1초 기준 통과 |
| 최대 응답시간 | 28.11초 | 극단적 이상치가 한 번 이상 존재; 백분위 통과와 별개로 원인 확인 필요 |
| dropped iterations | 0 | 목표한 요청을 모두 발생시킴 |
| 최대 사용 VU | 24 | 30 RPS 구간까지 유지하는 데 최대 24개 실행 슬롯 사용 |
| 수신 데이터 | 1.9GB | 상품 목록 응답 본문이 커 네트워크 비용·응답 크기 최적화 후보임 |
| 시나리오 시간 | 32분 완료 | 정의한 5단계를 모두 수행 |
| wall-clock 표시 | 41분 40.6초 | 약 9분 40초 차이가 있어 실행 PC 정지/절전 여부 확인 필요 |

판정: **기능 및 Threshold 통과, 시간 무결성 주의**. 상품 목록 API는 이 실행에서
30 RPS 지속 구간을 오류나 dropped iteration 없이 처리했다. 다만 wall-clock 차이와
28.11초 이상치가 있으므로 다음 공식 비교 시험은 절전을 차단하거나 별도 AWS 부하
발생기에서 수행한다.

원본 k6 출력과 더 상세한 해석은
[`results/2026-09-25-products-list-stage2.md`](results/2026-09-25-products-list-stage2.md)에
보존한다.

## 6. 실행 순서

### 6.1 Smoke

```bash
BASE_URL=https://www.seonjalal.com \
PROFILE=smoke \
k6 run load-tests/k6/products-list.js
```

모든 검사와 HTTP 요청이 100% 성공하지 않으면 다음 단계로 진행하지 않는다.

### 6.2 Baseline

```bash
BASE_URL=https://www.seonjalal.com \
PROFILE=baseline \
ALLOW_PRODUCTION=true \
k6 run load-tests/k6/products-list.js
```

### 6.3 Load

```bash
BASE_URL=https://www.seonjalal.com \
PROFILE=load \
ALLOW_PRODUCTION=true \
k6 run load-tests/k6/products-list.js
```

운영에서 실행할 때는 담당자와 시간대를 정하고, 실제 사용자 영향·컨테이너 재시작·DB
연결 고갈·지속적인 CPU 90% 이상이 나타나면 중단한다. Baseline과 Load는 실수로 운영에
부하를 주지 않도록 `ALLOW_PRODUCTION=true`가 없으면 실행되지 않는다.

## 7. CLI 복사 없이 결과 파일 만들기

k6 자체 Web Dashboard는 실행 중 그래프를 보여주고 종료 후 독립 HTML 파일로 내보낼
수 있다. 별도 Reporter를 반드시 설치할 필요는 없다.

로컬 Browser에서 실행 중 그래프까지 보려면 다음과 같이 실행한다.

```bash
K6_WEB_DASHBOARD=true \
K6_WEB_DASHBOARD_OPEN=true \
K6_WEB_DASHBOARD_EXPORT=load-tests/results/baseline-report.html \
BASE_URL=https://www.seonjalal.com \
PROFILE=baseline \
ALLOW_PRODUCTION=true \
k6 run load-tests/k6/products-list.js
```

Dashboard는 기본적으로 `http://127.0.0.1:5665`에서 열리며 요청률, VU, 응답시간
백분위, 오류와 Check 결과의 시간별 변화를 보여준다.

CI처럼 Browser가 필요 없는 환경에서는 다음과 같이 Dashboard Port를 끈다.

```bash
K6_WEB_DASHBOARD=true \
K6_WEB_DASHBOARD_PORT=-1 \
K6_WEB_DASHBOARD_EXPORT=load-tests/results/baseline-report.html \
BASE_URL=https://www.seonjalal.com \
PROFILE=baseline \
ALLOW_PRODUCTION=true \
k6 run load-tests/k6/products-list.js
```

- `K6_WEB_DASHBOARD_EXPORT`: 그래프와 통계를 HTML 파일로 저장한다.
- `K6_WEB_DASHBOARD_PORT=-1`: 브라우저용 로컬 서버를 열지 않고 파일만 만든다.
- 사람에게 공유할 결과는 HTML과 해석 Markdown을 함께 보관한다.

원시 시계열까지 재분석하려면 JSON을 추가한다.

```bash
BASE_URL=https://www.seonjalal.com \
PROFILE=baseline \
ALLOW_PRODUCTION=true \
k6 run --out json=load-tests/results/baseline-raw.json.gz \
  load-tests/k6/products-list.js
```

JSON은 크기가 매우 커질 수 있으므로 Git에는 요약 Markdown과 필요한 HTML만 남기고,
원시는 Artifact/Object Storage에 보존하는 편이 낫다. 장기적으로는 InfluxDB·Prometheus
remote write·Grafana Cloud 같은 출력으로 전송해 애플리케이션 지표와 같은 시간축에서
비교할 수 있다.

## 8. 시험 전 관측 체크리스트

현재 V1은 단일 EC2에 Nginx, Backend와 MySQL 컨테이너가 함께 있으므로 한 자원의 포화가
전체 서비스에 영향을 준다. k6 숫자만 저장하지 말고 같은 시작·종료 시각으로 아래를
기록한다.

| 계층 | 최소 기록 항목 | 이유 |
|---|---|---|
| k6 | RPS, 요청 수, 실패율, p50/p95/p99, dropped iterations | 사용자에게 보인 결과 |
| EC2 | CPU, memory, disk, network, status check | 호스트 병목 여부 |
| Docker | 컨테이너 CPU/memory, health, restart count | 어떤 컨테이너가 원인인지 구분 |
| Backend | 5xx, JVM heap/GC/thread, Hikari connection | 애플리케이션·DB pool 병목 구분 |
| MySQL | connection, slow query, lock wait, CPU/memory | DB 병목 구분 |
| 변경 조건 | Git commit, 이미지 tag, 인스턴스 유형, 데이터량 | 서로 다른 실행을 공정하게 비교 |

월요일 출시 전 최소선과 이후 확장안은 [`SLI-SLO.md`](SLI-SLO.md)에 정의한다.

## 9. 다음 테스트

1. AWS의 별도 부하 발생기에서 같은 30 RPS 시나리오를 재현해 시간 무결성을 확인한다.
2. 상품 상세, 선물 이력, 로그인 등 핵심 API를 각각 분리해 Baseline을 측정한다.
3. 실제 Access Log에서 API 비율을 구해 혼합 Workload를 만든다.
4. 쓰기 API는 테스트 계정·멱등성·데이터 정리 전략을 준비한 뒤 실행한다.
5. V1 한계 발견용 stress/spike/soak 시험은 운영이 아닌 격리 환경에서 수행한다.

## 10. 인증 사용자 조회 여정과 데이터 보존 원칙

선잘알은 로그인 후 사용하는 서비스이므로 사용자 여정 부하 테스트는 비로그인 Traffic을
제외한다. 첫 인증 여정은 `k6/authenticated-read-journey.js`이며 다음 요청을 순서대로
실행한다.

```text
상품 카테고리
→ 상품 목록
→ 목록에서 productId 선택
→ 상품 상세
→ 보낸 선물 목록과 상세
→ 받은 선물 목록과 상세
```

이 스크립트는 로그인 API를 반복 호출하지 않는다. 1시간 유효한 Access Token을 실행 시점에
`ACCESS_TOKEN` 환경변수로 주입하며, Token이나 비밀번호를 저장소 파일에 기록하지 않는다.
현재 Backend 로그인은 새 Refresh Token Session을 `user_sessions`에 저장하므로 로그인을
매 iteration에 포함하면 조회 여정만 시험해도 Session Row가 계속 증가할 수 있기 때문이다.

현재 인증 여정은 GET 요청만 사용하므로 상품, 선물, 사용자 Business Data를 생성하지 않는다.
다만 Token을 발급받기 위한 로그인 1회는 Session Row를 만들 수 있다. 만료 Session 정리는
테스트 Script의 `teardown()`에만 의존하지 않고 Backend의 보존 정책과 Scheduled Cleanup으로
관리해야 한다. 강제 중단·Network 오류 상황에서는 teardown이 항상 정리를 보장할 수 없기
때문이다.

쓰기 API 부하 테스트를 추가할 때는 다음 원칙을 사용한다.

1. Production이 아닌 격리된 Develop DB에서 실행한다.
2. Test User와 생성 Row에 Run ID 또는 명확한 Test Ownership을 남긴다.
3. 실행 전 DB Snapshot 또는 Backup을 만든다.
4. 정상 종료 후 Run ID 범위만 삭제하는 Cleanup Job을 실행한다.
5. 비정상 종료에 대비해 TTL 기반 Scheduled Purge를 추가한다.
6. 삭제 전 대상 Row 수를 조회하고 Foreign Key 순서에 맞춰 삭제한다.

HTTP 요청 여러 개에 걸친 사용자 여정은 하나의 DB Transaction으로 Rollback할 수 없다.
각 API가 독립 Transaction으로 Commit되므로, 쓰기 시험은 Snapshot Restore 또는 명시적
Cleanup Query/관리 기능이 필요하다.

### 인증 조회 Smoke 실행

Browser에서 로그인한 뒤 개발자 도구 Console에서 다음 명령으로 Access Token을 Clipboard에
복사한다.

```javascript
copy(localStorage.getItem('accessToken'))
```

Mac Terminal에서 Token이 Shell History에 남지 않도록 숨김 입력으로 받는다.

```bash
read -s ACCESS_TOKEN
export ACCESS_TOKEN

BASE_URL=https://www.seonjalal.com \
PROFILE=smoke \
ACCESS_TOKEN="$ACCESS_TOKEN" \
k6 run load-tests/k6/authenticated-read-journey.js

unset ACCESS_TOKEN
```

보낸 선물과 받은 선물 상세까지 반드시 실행됐는지 검증하려면 선물 이력이 각각 하나 이상인
Test Account를 사용하고 엄격 모드를 켠다.

```bash
BASE_URL=https://www.seonjalal.com \
PROFILE=smoke \
REQUIRE_GIFT_DETAILS=true \
ACCESS_TOKEN="$ACCESS_TOKEN" \
k6 run load-tests/k6/authenticated-read-journey.js
```

- `sent_gift_detail_coverage`: 보낸 선물 상세 분기를 실행한 Journey 비율
- `received_gift_detail_coverage`: 받은 선물 상세 분기를 실행한 Journey 비율
- 엄격 모드에서 두 Coverage는 모두 100%여야 한다.
- 선물 목록이 비어 있는 사용자는 유효한 사용자 상태이므로 기본 Smoke에서는 실패로 처리하지
  않지만, 해당 실행을 선물 상세 성능 근거로 사용해서는 안 된다.

Smoke가 통과한 뒤에만 3 VU, 5분의 낮은 Baseline을 실행한다. 3 VU는 확정된 실제 동시 사용자
수가 아니라 첫 측정을 위한 가설이며 Access Log의 실제 동시성과 API 비율로 보정한다.

```bash
read -s ACCESS_TOKEN
export ACCESS_TOKEN

BASE_URL=https://www.seonjalal.com \
PROFILE=baseline \
ALLOW_PRODUCTION=true \
ACCESS_TOKEN="$ACCESS_TOKEN" \
K6_WEB_DASHBOARD=true \
K6_WEB_DASHBOARD_PORT=-1 \
K6_WEB_DASHBOARD_EXPORT=load-tests/results/authenticated-read-baseline.html \
k6 run load-tests/k6/authenticated-read-journey.js

unset ACCESS_TOKEN
```

## 참고 자료

- [k6 Web Dashboard와 HTML export](https://grafana.com/docs/k6/latest/results-output/web-dashboard/)
- [k6 결과 출력 방식](https://grafana.com/docs/k6/latest/get-started/results-output/)
- [k6 JSON output](https://grafana.com/docs/k6/latest/results-output/real-time/json/)
- [JMeter HTML Dashboard](https://jmeter.apache.org/usermanual/generating-dashboard)
- [nGrinder 공식 저장소](https://github.com/naver/ngrinder)
