# AI 운영 이미지 저장소

- 별도 Terraform State: `v2/production/ai-artifacts/terraform.tfstate`. 기존 BE·스테이징 ECR State 변경 없음
- 운영 전용 `seonjalal/production/ai-chat`, `ai-migrate`, `ai-profiler`, `ai-search` 네 저장소 생성
- Immutable 태그, Push 시 이미지 스캔, AES256 암호화, 강제 삭제 금지·Terraform 삭제 방지
- 미태그 이미지만 7일 후 만료. 태그된 Release의 보존/정리는 별도 정책 결정 전까지 수동 관리
- AI `main`의 불변 owner/repository ID OIDC subject만 수임하는 운영 전용 역할 추가. ECR 로그인 외에는 네 운영 저장소의 업로드·조회만 허용
- AI Workflow·실제 이미지 Push·ECS Task/Service는 이 Root에서 생성하지 않음. 스테이징 발행 역할과 분리
- Apply 시 이미지 저장 용량 및 스캔에 따른 비용 발생 가능. 병합 뒤 계정·서울·운영 State와 저장 Plan 검토 및 별도 승인 필요
- `backend.hcl`, 실제 `terraform.tfvars`, State, Plan, Secret은 커밋 금지

참고: [ECR 이미지 태그 불변성](https://docs.aws.amazon.com/AmazonECR/latest/userguide/image-tag-mutability.html), [ECR 수명 주기 정책](https://docs.aws.amazon.com/AmazonECR/latest/userguide/LifecyclePolicies.html)
