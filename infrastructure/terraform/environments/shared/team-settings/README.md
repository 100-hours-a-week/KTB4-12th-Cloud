# 팀별 비운영 설정 접근

개인 IAM 계정4개, 기존fs-theo조회, 팀별그룹2개, 팀/환경별 빈Secret4개와MFA필수설정접근권한 구성.

- Fullstack: fs-neo/fs-theo/fs-ava, AI: ai-dylan/ai-emet
- 담당Secret: `/seonjalal/{develop|staging}/teams/{fullstack|ai}`
- 담당일반설정: `/seonjalal/{develop|staging}/config/{fullstack|ai}/<KEY>`
- 기존DB/JWT Secret·타팀·Production·release배포기준값·SSM서버명령접근제외
- 새Secret4개는보관비용발생. 기존Secret값복사/삭제나운영·QA재배포는수행하지않음
- 초기비밀번호/AccessKey/Secret값/파라미터값은Terraform에생성하지않음. MFA등록·자기비밀번호변경만자가관리허용,초기로그인은Cloud별도절차

## 적용 순서

사용자가Mac Cloud저장소에서직접실행. Plan결과검토/승인후별도Apply,동시실행금지.

```bash
# 별도State초기화 — 기존Develop/Staging/운영State와분리
terraform -chdir=infrastructure/terraform/environments/shared/team-settings init -backend-config=backend.hcl
```

```bash
# 생성계획저장 — AWS실제변경없음
terraform -chdir=infrastructure/terraform/environments/shared/team-settings plan -out=team-settings.tfplan
```

Plan검토후Apply단계를별도안내. 기존fs-theo의AdministratorAccess/AmazonS3FullAccess는이Terraform코드에서자동회수하지않음. 팀그룹·정책반영확인후Cloud가해당계정의두정책만분리하는절차로수행. Theo를위한권한회수승인은확보했으나실제회수전후정책목록·시뮬레이션검증필요. 새정책추가만으로fs-theo의광역권한제거완료표시금지.

MFA로검증된세션에서본인팀비운영설정읽기/쓰기허용,타팀·운영·플랫폼/배포경로·서버조작거부를검증. Parameter상위경로의재귀조회도거부확인. 일반콘솔목록을위한전계정Secrets/Parameters나열권한은부여하지않으므로정확한리소스주소로접근필요.

팀별저장소를현재배포로더/ECS정의에연동하기전까지기존런타임값유지. 이기반생성만으로팀입력값자동주입완료는아님. 현재설정권한은운영값을조회하거나배포를실행하는권한이아님.

## 운영 로그 읽기 전용 접근 (opt-in)

`production_log_read_enabled=true`일 때만 공통 관리형 정책 `SeonjalalTeamProductionLogRead`를 두 팀 그룹에 연결. 기본값 false는 리소스 0개.

- 허용: 운영 ECS 로그 그룹 4개(`/ecs/seonjalal-v2-production-{main-backend,payment-server,ai}`, 휴면 구 Backend `/ecs/seonjalal-v2-production/backend`)의 이벤트 조회·Logs Insights 쿼리. MFA·서울 리전 조건 필수
- 제외: 서버 셸(SSM), 로그 쓰기·삭제·보존기간·구독 변경(명시 Deny), Secret 값. 운영 EC2 접근 권한은 그대로 없음
- `Resource: "*"`는 리소스 지정이 불가능한 `DescribeLogGroups`·`GetQueryResults`·`StopQuery`·`DescribeQueries` 4개로 한정
- 로그에 토큰·개인정보가 섞일 수 있으므로 원문 전체 복사·스크린샷 공유 금지. 보존 기간은 30일
- 로그 그룹 이름은 `be_split_runtime`·`ai_ecs_runtime` 모듈의 이름 규칙과 일치해야 함. 모듈 이름 규칙이 바뀌면 `production-log-read.tf`도 함께 갱신

```bash
# Mac Cloud저장소: 변경 계획 저장. AWS 실제 변경 없음. 정책 1개 + 그룹 연결 2개 생성 예상
terraform -chdir=infrastructure/terraform/environments/shared/team-settings plan \
  -var production_log_read_enabled=true -out=production-log-read.tfplan
```

## 스테이징 로그 읽기 전용 접근 (opt-in)

`nonproduction_log_read_enabled=true`일 때만 `SeonjalalTeamStagingLogRead`를 두 팀 그룹에 연결. 운영 정책과 분리.

- 대상: `/ecs/seonjalal-v2-staging/backend`, `/ecs/seonjalal-v2-staging-{main-backend,payment-server,ai}`, 스테이징 AI PostgreSQL 로그. 존재하지 않는 로그 그룹은 허용만 있고 대상이 없음
- 운영 RDS 로그(AI PostgreSQL, MySQL 에러 로그)는 `production-log-read.tf` 목록에 포함. MySQL 로그 그룹은 foundation Root에서 내보내기를 켠 뒤에 생성
- 개발(develop)은 CloudWatch 로그 그룹이 없어 대상 아님. SSM 셸의 `docker logs`로 확인
- 모든 정책 적용 시 기존 `-var team_settings_admin_enabled=true -var team_ssm_access_enabled=true`를 반드시 함께 지정(미지정 시 현재 적용된 권한이 기본값으로 되돌아가는 계획이 생성됨)

## 로컬 검사

```bash
# AWS연결없는구문및mock정책범위검사
terraform -chdir=infrastructure/terraform/environments/shared/team-settings validate
terraform -chdir=infrastructure/terraform/environments/shared/team-settings test
```

정책상세설계·작업기록은로컬docs에보존,코드Repository/Wiki에자동게시하지않음.
