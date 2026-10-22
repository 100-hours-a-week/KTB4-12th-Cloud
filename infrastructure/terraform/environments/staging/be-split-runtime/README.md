# 스테이징 BE 분리 ECS Root

운영 출시가 먼저다. 이 Root는 현재 `deployment=null`이므로 아무 리소스도 만들지 않는다. 향후 스테이징 배포 시, AI Runtime Root가 Service Connect namespace를 먼저 준비한 뒤 그 ARN을 입력한다. 기존 `staging/rehearsal` Root의 단일 Backend Service와 ALB 기본 Target은 유지한다.

백엔드 S3 State Key는 `v2/staging/be-split-runtime/terraform.tfstate`로 독립적이다. `backend.hcl.example`은 예시이며 실제 `backend.hcl`, 실제 `terraform.tfvars`, Secret 값, Plan/State 파일은 커밋하지 않는다.

`deployment` 입력 계약은 공통 모듈 `variables.tf`에 정의한다. 클러스터/Capacity Provider/ALB Listener 및 SG는 기존 Rehearsal Root의 실출력과 조회로 확인한다. `namespace_arn`은 스테이징 AI Runtime Root의 출력이다. `database_host`/`database_name`/SG는 최신 데이터를 검증한 MySQL 대상으로 지정한다. ECR 이미지는 Main/Payment 각 Digest, 동일 SHA의 검증된 릴리스로 입력한다. Main/Payment DB 계정과 비밀 값은 JSON Secret의 키로만 주입한다.

배포 절차는 운영 출시 이후 별도 승인으로 수행한다. 먼저 `desired_count=0`, `traffic_cutover=false`로 저장 Plan을 검토하고 적용한다. 최신 MySQL Migration/Secret/이미지/네트워크/두 AZ 용량을 검증한 뒤 Payment → Main 순으로 각2개를 기동하고 Health·내부 호출을 확인한다. 마지막으로 Listener Host Rule을 별도 Plan으로 검토해 스테이징 트래픽을 전환한다. 실패 시 Rule을 제거해 기존 기본 Target으로 돌리고, DB 변경은 별도 복구 절차를 사용한다.

기존 Rehearsal ALB의 Idle Timeout은 60초다. Chat SSE의 응답 간격/Heartbeat가 이보다 길면 연결이 끊길 수 있으므로 실제 배포 전 별도 Root 변경과 QA가 필요하다. 신규 Main Task의 상품 S3 권한은 확인한 Prefix의 GetObject만 포함하며, 상품 업로드 또는 고객 관리 KMS 키 사용은 별도 권한 검토가 필요하다.

`terraform init -backend=false`·`terraform validate`·`terraform test`는 AWS 리소스를 만들지 않는다. 실제 `plan/apply` 전에 대상 계정/리전/State Key/변경 목록을 확인한다.
