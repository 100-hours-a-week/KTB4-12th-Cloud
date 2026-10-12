# V2 AWS Inventory와 Terraform Bootstrap 준비

- 조사일: 2026-10-06
- 대상 계정: `<V2_AWS_ACCOUNT_ID>`
- 대상 Region: `ap-northeast-2`
- 변경 영향: AWS 읽기 전용 조회만 수행, Resource 생성·변경·삭제 없음

## 결론

현재 계정에는 V1 EC2와 기본 VPC만 있으며 V2 ECS, ECR, RDS, ALB, ASG와 전용 Network는 없다.
V2는 기본 VPC나 V1 Security Group을 재사용하지 않고 `10.20.0.0/16` 전용 VPC에서 Develop부터
구축한다. 기존 Route 53 Hosted Zone과 GitHub OIDC Provider는 Account 공용 Resource로 재사용할 수
있지만, IAM Role·Terraform State·Service Resource는 환경별로 분리한다.

## 읽기 전용 Inventory

| 영역 | 확인 결과 | V2 판단 |
|---|---|---|
| AWS Identity | V2 전용 IAM 주체, Account `<V2_AWS_ACCOUNT_ID>` | Local Plan/Apply 주체, CI는 별도 OIDC Role |
| VPC | 기본 VPC `172.31.0.0/16` 한 개 | V2에서 재사용하지 않음 |
| Subnet | 기본 VPC Public Subnet 4개, 모두 Public IP 자동 할당 | V2 Public/App/Data Subnet 별도 생성 |
| EC2 | V1 Production `t3.medium` 실행, V1 Develop `t3.medium` 중지 | V2 State에서 관리하지 않음 |
| Security Group | V1 Production, 과거 Staging, Default SG | V2 ALB/ECS/RDS SG 별도 생성 |
| ECS/ECR/RDS/ALB/ASG | 모두 없음 | 이름 충돌 없이 신규 구축 가능 |
| NAT Gateway/VPC Endpoint | 모두 없음 | 비용과 외부 AI API 경로를 비교해 별도 결정 |
| S3 | Develop 상품 Image Bucket 한 개 | State·Frontend Bucket으로 재사용하지 않음 |
| Route 53 | `seonjalal.com` Public Hosted Zone | 환경별 Record만 추가 가능 |
| ACM | 서울·버지니아 모두 인증서 없음 | ALB와 CloudFront 인증서 신규 필요 |
| GitHub OIDC | `token.actions.githubusercontent.com` Provider 존재 | Provider 재사용, Role은 V2 환경별 분리 |
| Secret | Production Discord Webhook Secret 한 개 | V2 Application Secret 별도 생성 |
| CloudWatch Alarm | V1 EC2 Alarm 3개, Action ARN 비어 있음 | V1 후속 작업으로 분리 |
| AWS Budget | 없음 | V2 Apply 전에 월 Budget·알림 필요 |

Secret Value, Access Key와 Password는 조회하지 않았다.

## V2 Develop 경계

### 이름

Resource Prefix는 `seonjalal-v2-develop`을 사용한다.

예시:

- VPC: `seonjalal-v2-develop-vpc`
- ECS Cluster: `seonjalal-v2-develop-cluster`
- Backend Service: `seonjalal-v2-develop-backend`
- RDS: `seonjalal-v2-develop-mysql`
- ALB: AWS 길이 제한을 고려해 `seonjalal-v2-dev-alb`

### 필수 Tag

| Key | Value |
|---|---|
| `Project` | `seonjalal` |
| `Environment` | `develop` |
| `Version` | `v2` |
| `ManagedBy` | `terraform` |
| `Owner` | `cloud` |
| `Repository` | `KTB4-12th-Cloud` |
| `CostCenter` | `ktb4-12th` |

### Network

| Tier | AZ-a | AZ-c | Public IP |
|---|---|---|---|
| Public/ALB | `10.20.0.0/24` | `10.20.1.0/24` | ALB Node만 AWS 관리 |
| Private App/ECS | `10.20.10.0/24` | `10.20.11.0/24` | 없음 |
| Private Data/RDS | `10.20.20.0/24` | `10.20.21.0/24` | 없음 |

AZ는 `ap-northeast-2a`, `ap-northeast-2c`를 초기값으로 사용한다. VPC CIDR은 현재 기본 VPC와
겹치지 않는다. 향후 VPC Peering, VPN 또는 사내 Network와 연결하기 전에는 상대 CIDR을 다시
검증한다.

### State 분리

| Root | State Key | 책임 |
|---|---|---|
| Bootstrap | `bootstrap/state/terraform.tfstate` | State Bucket 자체 |
| Develop Foundation | `v2/develop/foundation/terraform.tfstate` | Network·공통 IAM·ECR·예산 기반 |
| Develop Data | `v2/develop/data/terraform.tfstate` | RDS·Backup·DB Secret |
| Develop Platform | `v2/develop/platform/terraform.tfstate` | ALB·ECS·ASG·Auto Scaling |
| Develop Observability | `v2/develop/observability/terraform.tfstate` | P/G/L과 Alert 연동 |

이번 작업에서는 Bootstrap과 Foundation 경계만 코드로 만들었다. Data, Platform과 Observability Root는
해당 ADR 승인 후 추가한다.

## Terraform Bootstrap 보호 장치

- 대상 Account를 `allowed_account_ids`로 제한한다.
- State Bucket은 Public Access를 모두 차단하고 TLS가 아닌 요청을 거부한다.
- Versioning과 기본 SSE-S3 암호화를 활성화한다.
- `force_destroy=false`, `prevent_destroy=true`를 적용한다.
- Terraform S3 Native Lock File인 `use_lockfile=true`를 사용한다.
- 최초 Bucket Apply까지만 Local State를 사용하고 즉시 `bootstrap/state/terraform.tfstate`로 이전한다.
- 실제 `terraform.tfvars`, State와 Plan은 Git에 Commit하지 않는다.

## 검증 명령

아래 명령은 Mac에서 실행한다. 현재 단계에서는 `apply`를 실행하지 않는다.

```bash
# Bootstrap 코드 형식과 Provider 구성을 초기화한다.
terraform -chdir=infrastructure/terraform/bootstrap/state init

# AWS Resource를 만들지 않고 구성 문법과 Provider 계약을 검사한다.
terraform -chdir=infrastructure/terraform/bootstrap/state validate

# State Bucket 생성 예정 항목만 확인하고 Plan 파일은 Git에 Commit하지 않는다.
terraform -chdir=infrastructure/terraform/bootstrap/state plan \
  -var='aws_profile=seonjalal-production'

# Remote State가 아직 없으므로 Develop Foundation은 Backend 연결 없이 정적 검증한다.
terraform -chdir=infrastructure/terraform/environments/develop/foundation init -backend=false

# 실제 Resource가 없는 경계 Root의 문법과 변수 검증만 수행한다.
terraform -chdir=infrastructure/terraform/environments/develop/foundation validate
```

## 실제 적용 전 남은 Gate

1. State Bucket 이름과 필수 Tag 승인
2. Bootstrap Plan의 Create/Update/Delete 수와 예상 비용 확인
3. V1 Resource Delete/Replace 0건 확인
4. Terraform Apply 주체를 Local IAM User로 할지 별도 Bootstrap Role로 할지 결정
5. AWS Budget 금액과 알림 수신 경로 결정
6. 명시적 승인 후 State Bucket만 Apply
7. Local Bootstrap State를 S3 Backend로 이전하고 State Version을 확인

## 이번 검증 결과

- `terraform fmt -check -recursive infrastructure/terraform`: 통과
- Bootstrap `terraform init`: 기존 Alerting Directory에 설치된 AWS Provider `6.67.0`을 로컬
  Mirror로 재사용해 통과
- Develop Foundation `terraform init -backend=false`: 같은 Local Provider로 통과
- Bootstrap `terraform validate`: 통과
- Develop Foundation `terraform validate`: 통과
- 두 `.terraform.lock.hcl`에 AWS Provider `6.67.0`의 Registry Checksum을 반영해 macOS 한 Platform의
  Local Checksum만 남는 상태를 보완
- Bootstrap `terraform plan -detailed-exitcode`: `6 add / 0 change / 0 destroy`, 종료코드 2로 정상 통과

Provider를 Local Mirror에서 초기화할 때 출력된 `unauthenticated`는 Provider Binary를 새로 내려받아
검증하지 않고 기존 Directory의 Binary를 재사용했다는 의미다. 같은 Binary는 기존 Alerting
Terraform의 Lock File에 기록된 Registry Checksum과 대조해 새 Lock File에 반영했다. CI에서는
Registry에서 Lock File Checksum으로 Provider를 다시 검증해야 한다.

현재 완료 상태는 **State Bucket Apply·보호 검증·S3 Backend 이전·무변경 Plan 완료**다. Apply 결과는
`6 add / 0 change / 0 destroy`였고 Public Access Block, Versioning, SSE-S3, BucketOwnerEnforced,
비공개 Policy와 TLS 강제를 읽기 전용으로 확인했다. Bootstrap State는 S3 Key
`bootstrap/state/terraform.tfstate`로 이전됐고 Remote Backend 기준 Plan은 `No changes`다.

## 다음 작업

State Bootstrap 승인 전에도 다음 문서 작업은 안전하게 진행할 수 있다.

1. Bootstrap Apply의 생성 대상·비용·복구 방법 설명과 명시적 승인
2. State Bucket Apply 후 Bucket 보호 설정 검증
3. Local Bootstrap State를 S3 Remote Backend로 이전
4. Network Egress의 NAT Gateway·NAT Instance·VPC Endpoint 조합과 월 비용 ADR
5. Develop/Production IAM OIDC Role과 최소 권한 경계
6. ECR Repository, Tag 불변성, Scan과 Lifecycle ADR
7. Foundation Module의 VPC·Subnet·Route·Security Group 구현

AWS Resource 생성은 위 설계와 Plan 검토 뒤에만 수행한다.
