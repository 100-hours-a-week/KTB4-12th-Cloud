# AI 운영 ECS 실행 경계

- 독립 State: `v2/production/ai-runtime/terraform.tfstate`. 기존 운영 BE Service·Listener·ASG와 분리
- 기본 `deployment = null`: AWS 리소스 0개. Task Definition/Service를 이미 등록했다는 뜻이 아님
- 실제 Digest·DB·SG·자산/Manifest·Backend 계약 입력 시 `desired_count=0`으로 Task Definition·Service 및 Secret Metadata 준비 가능
- `desired_count=1` 기동은 DB Migration·Catalog·Network·Capacity·Runtime·Secret·Provider 비용 승인 체크가 모두 참일 때만 허용
- 서울 A Private App Subnet 한 곳에 AI 단일 Task 배치. 가용성보다 비용 우선이며 Task/Host/AZ 장애 중단을 허용
- AI Task 예약값 CPU2048·메모리4096MiB. 기존 운영 `t3.medium` ECS 호스트는 예약 메모리512MiB와 BE Task가 있어 AI 수용을 보장하지 않음. 별도 AI Capacity Provider·호스트 용량 검증 선행
- 운영 AI PostgreSQL Endpoint·Client SG를 사용. RDS 관리자 Secret을 Task에 주입하지 않고, pgvector/Role Bootstrap·Migration을 별도 승인 단계로 수행
- Secrets Manager에는 runtime/migration/catalog Secret Metadata만 생성. 실제 JSON 값은 Terraform State에 넣지 않음
- 동일 AI main SHA의 운영 ECR Digest 네 개, 승인된 모델 S3 Manifest·Hash, BE Export/Callback/Token 계약 필요. 초기 상품 이미지는 BE의 기존 S3 경로가 제공하며 AI `/media`는 비어 있음
- regional RDS 공개 CA는 `staging/rehearsal/certs`의 동일 서울 Bundle 파일을 코드 자산으로 재사용. 비밀키가 아니며 앱 DSN `sslmode=verify-full` 검증 필요
- Cloud CD의 AI 배포 경로는 아직 미구현. Terraform Service/Task 템플릿 등록과 실제 Revision 배포의 소유권을 구분
- 실제 `terraform.tfvars`, `backend.hcl`, State, Plan, Secret 값은 커밋 금지. 신규 비용/권한/Task 생성은 병합 후 저장 Plan과 별도 승인 필요
