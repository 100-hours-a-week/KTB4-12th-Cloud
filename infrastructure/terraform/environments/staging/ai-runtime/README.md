# AI 스테이징 ECS 실행 준비

- 상태: 코드·Mock 검증용. 운영 출시 전 스테이징 Apply·앱 기동·Migration 보류
- State key: `v2/staging/ai-runtime/terraform.tfstate`
- 기본 `deployment = null`: 모듈 미생성. Task Definition·Service가 AWS에 등록된 상태를 의미하지 않음
- 실제 이미지·DB·SG·자산 정보를 입력하고 desired_count=0으로 플랫폼을 준비할 수 있으나, 현재 일정에서는 이 Apply도 보류
- `../../../modules/ai_ecs_runtime` 재사용. 이번 변경은 스테이징 Root만 연결하며 운영 Root·기존 BE/ALB·ASG에는 미적용
- 실제 tfvars·Backend·State·Plan·Secret 값 커밋 금지

## 실제 기동 전 입력

- 동일 AI main SHA에 해당하는 chat/profiler/search/migrate의 해당 환경 ECR Digest 4개
- AI DB Root의 Endpoint·Client SG. DB bootstrap, pgvector·ai_migrator/서비스역할·Migration 및 초기 Catalog 준비 결과
- 서울 A Private App Subnet과 실제 ECS Cluster·Capacity Provider. Task CPU2048/메모리4096MiB와 Service Connect Proxy·호스트 여유 검증 필요
- Backend Main SG·callback 주소, 실제 Export/Categories HTTPS 경로 및 해당 ALB SG
- 모델·미디어 S3 Prefix와 승인된 manifest/hash. 모듈 README의 파일 형식 참조
- 승인된 Chat/Profiler 모델 및 Provider별 예산·키, 내부 토큰과 BE 공통 토큰의 매핑
- AWS Secrets Manager의 runtime/migration/catalog JSON 값. Terraform은 빈 Secret Metadata만 생성

## 현재 코드 호환성 주의

- 로컬 AI `integration/provider_key.py`의 AI_ENVIRONMENT는 local/develop/production만 허용. staging 지원을 추가·검증한 Image가 필요하며 develop으로 위장하지 않음
- 최신 코드의 ECS Secret 환경변수 주입을 사용. shared BE token과 internal token 분리, AI Console은 이번 배포에서 미포함
- 예산 모드는 최신 코드의 provider-key-limits 사용. 별도 Guarded Gateway를 설정한 것으로 간주하지 않으며 Provider Key 한도·운영 주체 확인 후 활성화
- 모델은 Image 내부 model.lock.json과 대조. 모델 사용 조건·모델/미디어 전달 책임은 AI팀 확인 필요
- Main/Payment는 이후 같은 Service Connect Namespace ARN을 사용. Backend의 ai-chat/ai-profiler 발견과 AI callback을 위해 양측 기동 순서·새 Task 생성 후 확인 필요
- Catalog 일회성 Task는 Service Connect 이름을 사용할 수 없으므로 실제 검증된 Backend HTTPS 진입점 사용
- 현재 Cloud `cd-v2.yml`은 AI 미지원. 이번 구성만으로 Cloud 버전 입력 배포가 완료되지 않으며 AI Delivery와 운영 ECR 승격은 후속 작업

## 확인 범위

- 로컬 fmt/validate·Mock·Bootstrap 단위 검사
- 미검증: 실제 Task 등록·모델 다운로드 시간·동일 Task3앱 기동·DB/Secret/Export 연동·SSE·실패복귀·Provider 호출
- 운영 출시 후 스테이징 실제 배포 시 새 저장 Plan 및 계정·서울·환경·비용 재확인 필요

## DB 부트스트랩(일회성, 스테이징)

- 운영 `production/ai-runtime`의 방식을 스테이징 DB(`ai_db_staging`)용으로 가져온 것. 빈 DB에 역할 5개와 pgvector를 만들고 DSN을 AI Secret 세 개에 기록한다. 앱은 시작하지 않는다.
- 기본 꺼짐: `bootstrap_admin_secret_arn`(RDS 관리형 `ai_admin` Secret)과 4시간 이내 `bootstrap_expires_at`을 입력하면 Task Definition·IAM 역할·Secrets 엔드포인트 규칙만 등록한다. Terraform은 Task를 실행하지 않는다.
- 운영과 다른 점: DSN 인증서 경로를 리터럴(`sslrootcert=/shared/tls/rds-ca.pem`)로 만들고 `ConfigParser` 라운드트립을 생성 시점에 검증한다. 운영은 `%2F` 인코딩 때문에 Alembic이 실패하고 출력에 비밀번호가 남은 사고(2026-10-22)가 있었다. 그 복구용 `rotate_migration`은 스테이징에 필요 없어 가져오지 않았다.
- 재실행 방지: 이미 마이그레이션됐거나 테이블이 있으면 중단하고, 기존 Secret에 예상 밖 키가 있으면 덮어쓰지 않으며, 기존 역할의 권한이 예상과 다르면 중단한다. 실패 시 예외 종류만 출력한다.
- 실행 순서(첫 켜기 리허설): `staging/ai-database` apply → AI 호스트 apply → 이 Root를 `desired_count=0`과 부트스트랩 입력으로 apply → 부트스트랩 Task 한 번 실행(AI 전용 용량 공급자, 작업 SG와 DB 클라이언트 SG) → `exitCode=0`과 성공 출력 확인 → 마이그레이션 Task 한 번 → 카탈로그 번들 적용 Task 한 번. 각 단계 사이에 CloudWatch 로그에 비밀이 없는지 점검한다.
- 검증: `terraform test`(Mock)와 `tests/test_bootstrap.py`(가짜 DB·가짜 Secrets Manager). 실제 DB 실행은 리허설에서 확인한다.
