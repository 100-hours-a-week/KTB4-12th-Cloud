# AI 운영 PostgreSQL 실행 경계

- 독립 State: `v2/production/ai-database/terraform.tfstate`. 기존 BE MySQL과 스테이징 AI DB State 변경 없음
- 기존 공유 Private Data Subnet 재사용. DB Subnet Group은 서로 다른 두 AZ·서울 A 포함, 실제 DB는 서울 A Single-AZ
- PostgreSQL 18.6, `ai_db_production`, 기본 `db.t4g.micro`·gp3 20GiB. 가용성/성능 보장값이 아닌 비용 우선 시작값
- 저장소 자동 확장 비활성. 공간·메모리·연결 수와 사용량을 관측하고 용량 부족 전에 변경 검토
- RDS 관리형 관리자 Secret 사용. Secret 값·실제 tfvars·backend·Plan 커밋 금지. 관리자 Secret을 앱 Task에 주입 금지
- Output의 Client SG는 운영 AI Service/Migration Task에만 연결. BE/스테이징 Task 연결 금지
- Terraform은 빈 DB만 생성. 별도 승인된 bootstrap으로 pgvector·`ai_migrator`·서비스 역할 생성 후 Migration 실행 필요
- 서버 TLS 강제와 클라이언트 인증서 검증은 별개. 앱 DSN의 `sslmode=verify-full` 및 RDS CA 전달을 ECS에서 검증 필요
- 삭제 보호·`prevent_destroy`·7일 백업·최종 Snapshot 설정. 실제 복원 Drill 전 RPO/RTO 달성으로 간주 금지
- 생성 즉시 DB·스토리지·로그 비용 발생. 운영 DB를 비용 절감 목적으로 자동 중지하지 않음
- 병합 후 대상 계정·서울·운영·기존 Data Subnet을 재확인하고 새 저장 Plan의 생성/변경/삭제 전체 검토. 사용자 별도 승인 전 Apply 금지

참고: [RDS PostgreSQL TLS](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/PostgreSQL.Concepts.General.SSL.html), [RDS 관리형 Secret](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/rds-secrets-manager.html), [RDS 백업](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_WorkingWithAutomatedBackups.html)
