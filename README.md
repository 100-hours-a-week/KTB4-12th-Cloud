# 선잘알 Cloud

## 선잘알 소개

선잘알은 사용자의 비선호 카테고리와 선물 이력을 반영하여 상황에 맞는 선물을 추천하고,
선물 선택의 불확실성을 줄이는 서비스입니다.

주요 기능은 다음과 같습니다.

- 비선호 카테고리를 반영한 상품 탐색
- 받은 선물 이력 조회
- 상황과 대상에 맞는 선물 추천

## 인프라의 역할

이 저장소는 선잘알 서비스의 실행 환경과 배포 구성을 관리합니다.

- Frontend, Backend, Database 컨테이너 실행 환경 관리
- Nginx를 통한 정적 파일 제공과 Backend API 연결
- 운영 환경의 Docker Compose 구성 관리
- GitHub Actions와 AWS Systems Manager를 이용한 운영 배포
- Health Check와 배포 후 검증
- 부하 테스트 코드와 운영 보조 도구 구성 관리

## 브랜치별 의미

| 브랜치 | 의미 |
|---|---|
| `main` | 운영에 반영할 수 있는 안정된 코드와 설정을 관리합니다. |
| `develop` | 기능 브랜치의 변경사항을 통합하고 운영 반영 전에 검증합니다. |
| `feat/*` | 새로운 인프라 기능이나 운영 구성을 추가합니다. |
| `fix/*` | 배포, 설정 또는 운영 과정에서 발견한 문제를 수정합니다. |
| `chore/*` | 의존성, 자동화, 저장소 설정 등 기능 외 유지보수 작업을 수행합니다. |

기능 작업은 별도 브랜치에서 진행한 뒤 `develop`에 병합하고, 검증이 끝난 변경사항을
`main`에 반영합니다.

## AI 서비스 미배포 상태의 Backend 실행

Backend `1.1.0`부터 AI 프로파일링 URL과 내부 서비스 토큰이 시작 시점의 필수 설정입니다.
AI 컨테이너를 아직 배포하지 않는 환경에서는 다음 값을 운영 `.env`에 설정합니다.

```dotenv
AI_PROFILE_BASE_URL=http://ai:8000
PROFILING_SERVICE_TOKEN=<환경별로 생성한 비밀값>
SCHEDULING_ENABLED=false
```

- `SCHEDULING_ENABLED=false`는 AI 프로파일링 배치 호출을 중지합니다.
- URL과 토큰은 Backend 설정 객체 생성에 필요하므로 AI 기능을 사용하지 않더라도 생략할 수 없습니다.
- `localhost`는 Backend 컨테이너 자신을 가리키므로 향후 AI 컨테이너 연동에는 `http://ai:8000`을 사용합니다.
- 실제 토큰은 저장소에 커밋하지 않고 운영 서버의 `.env`에서 관리합니다.
- AI 서비스가 배포되기 전까지 AI 프로파일링·추천 결과 갱신 기능은 동작하지 않습니다.

운영 배포 전에는 Backend Flyway 마이그레이션의 데이터 변경 범위를 확인하고 DB를 백업해야 합니다.
현재 자동 Rollback은 Frontend와 Backend 컨테이너만 이전 이미지로 복구하며 DB 변경은 되돌리지 않습니다.

## Develop 구성 가이드

Develop은 `develop` 브랜치의 통합 결과를 빠르게 검증하는 공유 환경이다. Production과 다른
EC2, Domain, DB Volume과 Secret을 사용하며 운영과 같은 Docker Compose·MySQL 8.4 구조를
유지한다. V1은 별도 사전 운영 환경을 두지 않고 `dev.seonjalal.com`을 사용한다.

### 1. Develop Host 준비

- Amazon Linux 계열 EC2를 별도로 준비한다.
- 외부 SSH를 열기보다 SSM Session Manager를 사용한다.
- Docker와 Docker Compose Major Version을 Production과 맞춘다.
- 배포 경로는 `/opt/seonjalal-develop`을 사용한다.
- 관리 접속은 SSM만 사용하고 SSH 22는 열지 않는다.
- HTTPS 443은 개발자·운영자 Public IPv4 `/32`만 허용한다.
- HTTP 80은 상시 공개하지 않고 인증서 발급 시에만 임시 허용하거나 DNS 검증을 사용한다.
- Container Port 8081과 MySQL 3306은 외부에 열지 않고 내부에서만 사용한다.

AWS Resource 생성·DNS 변경·인증서 발급은 대상 Account와 예상 비용을 확인한 뒤 수행한다.

### 2. 환경변수 준비

```bash
cp .env.develop.example .env.develop
chmod 600 .env.develop
```

`.env.develop`에서 Candidate Image Tag, Develop Bucket과 Secret을 실제 값으로 교체한다. Secret은
Production 값을 재사용하지 않으며 저장소에 Commit하지 않는다.

예시 Secret 생성 명령:

```bash
openssl rand -base64 48
openssl rand -base64 48
openssl rand -hex 32
```

작성한 환경 계약을 확인한다.

```bash
./scripts/validate-develop-env.sh .env.develop
```

### 3. Compose 구성 검증

```bash
docker compose \
  --env-file .env.develop \
  -f compose.yaml \
  -f compose.develop.yaml \
  config --quiet
```

Develop 전용 Host에서만 다음 기동 명령을 실행한다.

```bash
docker compose \
  --env-file .env.develop \
  -f compose.yaml \
  -f compose.develop.yaml \
  pull

docker compose \
  --env-file .env.develop \
  -f compose.yaml \
  -f compose.develop.yaml \
  up -d
```

### 4. 내부 Health 검증

```bash
ENV_FILE=.env.develop ./scripts/verify.sh develop
```

검증 항목은 Compose Configuration, Container 상태, Frontend 응답과 `/api/health` Routing이다.

### 5. Domain·TLS 연결

1. `dev.seonjalal.com` Route 53 A Record를 Develop IP에 연결한다.
2. `operations/develop/nginx/dev.seonjalal.com.conf.example`을 Host Nginx 기준으로 적용한다.
3. DNS 검증을 우선 사용하거나, HTTP 검증 동안만 Security Group의 80을 임시 허용한다.
4. 인증서 발급 후 80을 닫고 443 Source가 승인된 `/32` 목록뿐인지 확인한다.
5. 허용된 Network에서 Frontend와 `/api/health`를 확인한다.

```bash
curl -fsS https://dev.seonjalal.com/ >/dev/null
curl -fsS https://dev.seonjalal.com/api/health
```

권장 Develop Security Group Inbound 규칙:

| Port | Source | 비고 |
|---:|---|---|
| 443 | 개발자·운영자 Public IPv4 `/32` | 승인된 사용자 Browser·k6 접근 |
| 80 | 평소 규칙 없음 | HTTP 인증서 검증 시에만 임시 허용 |
| 22 | 규칙 없음 | SSM Session Manager 사용 |
| 8081·3306 | 규칙 없음 | Host Loopback·Docker Network 전용 |

외부 Inbound를 전혀 허용하지 않는 운영이 필요하면 SSM Port Forwarding으로 Local Port와
Develop Host의 8081을 연결해 `http://localhost:<local-port>`로 접근할 수 있다.

```bash
aws ssm start-session \
  --target <develop-instance-id> \
  --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["8081"],"localPortNumber":["18081"]}'
```

### 6. DB 격리 확인

- 실제 Volume 이름이 `seonjalal-develop-db-data`인지 확인한다.
- `DB_NAME`, `DB_USER`와 JWT Issuer에 `develop`이 포함되고 CORS Origin이
  `https://dev.seonjalal.com`인지 확인한다.
- 신규 DB에서 Flyway Migration이 성공했는지 Backend Log로 확인한다.
- Develop에서 생성한 Test User가 Production에 존재하지 않는지 확인한다.

Fixture 적재와 부하 테스트는 위 격리 확인과 Snapshot·Reset 절차가 완료된 뒤 진행한다.
