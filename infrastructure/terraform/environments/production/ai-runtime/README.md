# AI 운영 ECS 실행 경계

- 독립 State: `v2/production/ai-runtime/terraform.tfstate`. 기존 운영 BE Service·Listener·ASG와 분리
- 기본 `deployment = null`: AWS 리소스 0개. Task Definition/Service를 이미 등록했다는 뜻이 아님
- 실제 Digest·DB·SG·자산/Manifest·Backend 계약 입력 시 `desired_count=0`으로 Task Definition·Service 및 Secret Metadata 준비 가능
- `desired_count=1` 기동은 DB Migration·Catalog·Network·Capacity·Runtime·Secret·Provider 비용 승인 체크가 모두 참일 때만 허용
- 서울 A Private App Subnet 한 곳에 AI 단일 Task 배치. 가용성보다 비용 우선이며 Task/Host/AZ 장애 중단을 허용
- AI 전용 `t3.medium` Host는 ECS 등록 후 잔여 MEMORY 3327MiB·CPU2048을 실조회(2026-10-21). App Task는 CPU2048·메모리3200MiB, 컨테이너 Hard Limit 합계3008MiB로 Proxy192MiB와 Host 배치 여유127MiB를 남긴 최초 기동용 계약. 운영 입력은 BE용이 아닌 `seonjalal-v2-production-ai` Capacity Provider로 고정
- 이 계산은 배치 가능성만 다루며 안정적 실행의 증거가 아님. `capacity_verified=true` 입력 전 Service desired0과 등록 Host의 가용 CPU·메모리 대조 필요. Service Connect Proxy 포함 실제 사용량은 최초 desired1에서 관측하고, OOM·Health 실패 시 desired0으로 복귀한 뒤 Host 크기 또는 Task 분리 설계를 다시 결정
- 운영 AI PostgreSQL Endpoint·Client SG를 사용. RDS 관리자 Secret을 Task에 주입하지 않고, pgvector/Role Bootstrap·Migration을 별도 승인 단계로 수행
- Secrets Manager에는 runtime/migration/catalog Secret Metadata만 생성. 실제 JSON 값은 Terraform State에 넣지 않음
- 동일 AI main SHA의 운영 ECR Digest 네 개, 승인된 모델 S3 Manifest·Hash, BE Export/Callback/Token 계약 필요. 초기 상품 이미지는 BE의 기존 S3 경로가 제공하며 AI `/media`는 비어 있음
- regional RDS 공개 CA는 `staging/rehearsal/certs`의 동일 서울 Bundle 파일을 코드 자산으로 재사용. 비밀키가 아니며 앱 DSN `sslmode=verify-full` 검증 필요
- Cloud CD의 AI 배포 경로는 아직 미구현. Terraform Service/Task 템플릿 등록과 실제 Revision 배포의 소유권을 구분
- 실제 `terraform.tfvars`, `backend.hcl`, State, Plan, Secret 값은 커밋 금지. 신규 비용/권한/Task 생성은 병합 후 저장 Plan과 별도 승인 필요
