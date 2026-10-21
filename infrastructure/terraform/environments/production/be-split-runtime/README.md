# 운영 BE Main·Payment 분리 ECS Root

- 독립 State: `v2/production/be-split-runtime/terraform.tfstate`. 기본 `deployment = null`이면 AWS 리소스 0개다. 기존 운영 단일 BE Service, Target Group, ALB 기본 경로는 그대로 유지한다.
- 실제 배포 입력은 공통 `be_split_runtime` 모듈의 `config` 계약을 따른다. 두 개의 운영 ECR Digest는 동일 AI/BE 계약에 맞는 BE `main` 릴리스에서 확인한다. Secret 원문과 실제 tfvars는 Terraform State/저장소에 넣지 않는다.
- 운영 CloudFront VPC Origin은 기존 내부 ALB의 HTTPS 8443 Listener와 `v2-origin.seonjalal.com` Host를 사용한다. 기존 우선순위 10 규칙은 유지한다. 새 Main Target의 Health 확인 후 승인된 별도 Plan에서 우선순위 9 규칙으로만 Cutover한다. `www.seonjalal.com` FE/DNS를 이 Root에서 바꾸지 않는다.
- 첫 등록은 `desired_count=0`, `traffic_cutover=false`로 저장 Plan을 검토한다. AI Root가 Service Connect Namespace를 먼저 만들고 ARN을 제공해야 한다. 실제 AI Task가 아직 0개여도 Main·Payment Service 준비는 가능하지만 AI 기능은 정상이라고 주장할 수 없다.
- 운영 MySQL RDS는 현재 별도 데이터 Migration/Role 검증이 필요하다. 단순 DB Endpoint만으로 Main/Payment를 켜지 않는다. JDBC `sslMode=VERIFY_IDENTITY`, 환경별 DB 이름, Secret JSON Key를 검증한다.
- Main/Payment 각각 2개 기동에는 기존 BE 호스트 1대의 용량이 부족할 수 있다. 기존 BE ASG를 2대로 확대하고 두 AZ의 실제 배치 가능 메모리·CPU를 확인한 뒤 `capacity_verified`를 참으로 설정한다. 새 Service 활성화와 기존 단일 Service의 경합·ALB Target Health를 모니터링한다.
- Payment → Main 순서 기동, API/결제/AI 연동 검증, Host Rule Cutover는 모두 별도 승인 단계다. 실패 시 우선순위 9 규칙을 별도 Plan으로 제거해 기존 우선순위 10 Target으로 돌아간다. DB 변경은 별도 복구 절차를 따른다.
- 상품 S3 권한은 확인된 Prefix의 GetObject만 포함한다. 업로드 또는 고객 관리 KMS 키 사용은 별도 권한 검토가 필요하다.
- 서울 RDS 공개 CA Bundle은 기존 `staging/rehearsal/certs` 파일을 재사용한다. 비밀키가 아니다.
