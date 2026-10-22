# AI 스테이징 PostgreSQL 실행 경계

- 별도 State: `v2/staging/ai-database/terraform.tfstate`
- 기존 Private Data Subnet 재사용, DB Subnet Group은 서울 A 포함 두 AZ 구성
- 실제 DB는 PostgreSQL 18.6, `ai_db_staging`, Single-AZ 서울 A, 기본 `db.t4g.micro`·gp3 20GiB
- 저장소 자동 확장 비활성. 공간/메모리/연결 수 모니터링 후 용량 조정 필요
- RDS 관리형 관리자 Secret 사용. Secret 값·실제 tfvars·backend·Plan 커밋 금지
- output의 client Security Group은 AI Service/Migration Task에만 연결
- Terraform은 빈 DB만 생성. 관리자 Secret을 앱 Task에 주입하지 않으며, 별도 bootstrap으로 pgvector·`ai_migrator`·서비스 역할을 생성한 뒤 Migration 실행 필요
- 서버 TLS 강제와 클라이언트 인증서 검증은 별개. 앱 DSN의 `sslmode=verify-full`과 RDS CA 제공은 ECS 구성에서 검증 필요
- 삭제 보호 및 `prevent_destroy` 활성. 백업 보존·최종 Snapshot 검토 없이 삭제 또는 DB 재생성 금지
- 생성 직후 DB는 실행 상태이므로 비용 발생. 시험 종료 후 Stop은 별도 사용자 실행 절차로 수행하며, 최대 7일 후 자동 재시작 및 중지 중 저장소 비용에 주의
- 실제 초기화·Plan·Apply는 대상 계정/서울/스테이징과 기존 공유 Data Subnet을 확인한 뒤 사용자 직접 실행

참고: [RDS TLS](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/PostgreSQL.Concepts.General.SSL.html), [관리자 Secret](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/rds-secrets-manager.html), [일시 중지](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_StopInstance.html)
