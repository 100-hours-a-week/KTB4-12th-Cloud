# 현재 작업 상태

## 2026-10-11 V2 Develop Continuous Deployment 구축·최종 검증 완료

### 결론

- FE·BE 각 Repository의 `develop` Merge 성공 후 자동 배포되는 구조로 책임 경계 정정.
- Application Repository는 Image 발행과 자기 Component 희망 SHA 갱신, Cloud Repository는 IAM·상태·
  서버 재조정·Rollback 계약 소유.
- Develop은 승인 없는 Continuous Deployment, Production은 승인 기반 Continuous Delivery 유지.
- EC2 중지 중 Merge도 Parameter Store에 희망 SHA 보존 후 다음 부팅 시 자동 재조정하도록 설계.

### 완료한 작업

- FE·BE 희망 SHA용 SSM Parameter Store Terraform Resource 추가.
- FE·BE Repository·`develop` Branch별 Deploy OIDC Role과 자기 Parameter·대상 EC2 전용 권한 추가.
- 범용 `AWS-RunShellScript` 대신 고정 재조정 명령만 포함한 전용 SSM Document 추가.
- Host `flock`, Image URI 원자적 교체, Health 검증과 이전 Candidate 복구를 포함한 `reconcile.sh` 추가.
- EC2 부팅 시 미반영 희망 상태를 반영하는 systemd oneshot Service 추가.
- FE·BE Workflow에 `publish-develop` 성공 후 실행되는 `deploy-develop` Job 추가.
- 서버 Offline 시 배포 실패 대신 다음 부팅 대기, Online 시 SSM 명령 상태 최대 12분 확인 적용.
- Terraform Apply `7 added, 0 changed, 0 destroyed`와 Apply 직후 무변경 Plan 확인.
- Develop EC2에 재조정 Script와 systemd Unit 설치·Enable 완료.
- 전용 SSM Document 재조정에서 ECR Pull, Compose와 네 Container Health 검증 완료.
- FE·BE Repository의 Deploy Role ARN과 Develop Instance ID Variable 등록 완료.
- FE PR #177·BE PR #326 Merge와 두 Repository의 `validate`·ECR 발행·Develop 자동 배포 Job 성공.
- Workflow Merge SHA와 Parameter Store 희망 SHA 일치 확인.
- FE·Main Backend·Payment Server 실행 Image가 각 Merge SHA Tag와 일치함을 확인.
- 네 Container Health, Frontend·Backend Proxy, 임시 Secret·ECR Credential 제거 확인.
- Mac Tailscale 경로의 HTTPS·Backend Health와 Browser 화면 최종 확인.

### 검증한 항목

- Terraform `fmt -check` 통과.
- Runtime Shell Script Bash 문법 검사 통과.
- FE·BE Workflow YAML Parse 통과.
- `reconcile.sh` 실행 권한 `0750` 확인.
- 사용자 Terminal의 `terraform validate`와 Apply 후 무변경 Plan 통과.
- 최초 `/run/lock` 권한 오류 수정 후 재조정 통합 검증 통과.
- FE Workflow Run `38135320513`과 BE Workflow Run `38135337941`의 전체 Develop Job 성공.
- Tailscale Ping은 DERP Tokyo 중계 경로 사용, Frontend HTTPS 200과 Backend `UP` 확인.

### 남은 문제

- EC2 Offline Merge 후 부팅 재조정 시나리오의 실제 Stop·Merge·Start 통합 검증 미완료.
- GitHub Actions Node.js 20 강제 전환 경고와 BE `actions/setup-java@v4` 지원 종료 경고 정리 필요.
- 기존 FE React Hook·Fast Refresh Warning 정리 필요.

### 다음 작업

1. EC2 중지 중 Merge와 다음 부팅 자동 재조정 시나리오 검증.
2. GitHub Actions 의존 Action Version과 Runner 전환 경고 정리.
3. 팀원 초대 전 Tailscale Tag·Grant 최소 권한 적용과 접속 문서 공유.

상세 기록:

- `docs/2026-10-11-v2-develop-continuous-deployment.md`
- `docs/2026-10-11-v2-develop-runtime-cicd-final-report.md`

## 2026-10-11 V1 Benchmark 도메인 분리와 HTTPS 검증 완료

### 결론

- 기존 V1 비운영 EC2를 `v1-benchmark.seonjalal.com` 기준점으로 분리 완료.
- Route 53 A 레코드, 전용 ECDSA 인증서, Nginx Server Block과 Browser 화면 검증 완료.
- 현재 작업자 Public IPv4 `/32`를 Security Group과 Nginx Allowlist에 임시 허용.
- 신규 V2 Develop은 Public DNS가 아닌 Tailscale MagicDNS와 Serve HTTPS 사용 유지.
- 기존 `dev.seonjalal.com` A 레코드 제거와 V1 시험 기본 URL 변경 완료.
- V1 EC2에 Elastic IP가 없어 Stop·Start 후 A 레코드 갱신 필요.
- 수동 DNS-01 인증서는 자동 갱신되지 않으며 2027-01-09 이전 갱신 필요.

### 검증 결과

- V1 EC2 Running과 AWS System·Instance Status 정상.
- Frontend·Backend·Database Container Healthy.
- Route 53 변경 `INSYNC`와 Mac Resolver 일치.
- 신규 인증서 발급과 임시 DNS Challenge TXT 제거.
- Nginx 문법 검사와 Reload 성공.
- `https://v1-benchmark.seonjalal.com` HTTP 200과 Browser 화면 확인.

### 남은 문제

- 기존 `dev` Nginx 설정과 인증서 정리 시점 미결정.
- 현재 작업자 `/32` 접근 규칙의 시험 종료 후 제거 필요.
- V1 시작 시 Public IPv4와 Route 53을 동기화하는 Runbook 미작성.
- 인증서 자동 갱신 미구현.

### 다음 작업

1. Develop CD Workflow와 배포용 GitHub OIDC Role 구현.
2. 기존 `dev` Nginx 설정과 인증서의 안전한 제거 시점 결정.
3. V1 Benchmark 시작·DNS 갱신·시험 종료 Runbook 작성.

상세 기록: `docs/2026-10-11-v1-benchmark-domain-migration.md`

## 2026-10-11 V2 Develop Runtime 최초 배포와 Tailnet HTTPS 검증 완료

### 결론

- 기존 V1 Develop 서버를 V1 Benchmark 기준점으로 보존하고 신규 V2 Develop 전용 EC2를 별도 구축 완료.
- Public Subnet 배치와 Public IPv4 사용에도 Security Group Public Ingress 0개 유지.
- 관리 경로는 SSM, 사용자 Web 접근은 Tailscale Serve의 Tailnet 전용 HTTPS로 제한.
- FE·BE `develop` Merge의 ECR Image 자동 발행과 실제 Immutable Image 확인 완료.
- Secrets Manager를 Secret Source of Truth로 적용하고 상시 `.env`에서 실제 Secret 제거 완료.
- FE·Main Backend·Payment Server·MySQL 네 Container의 Health, Loopback, Tailnet HTTPS와 Browser 화면 확인 완료.
- CI Image 발행 완료, Develop CD 코드 구현 완료, Terraform·서버·Workflow 적용과 실제 검증 전.

### 완료한 작업

| 영역 | 완료 내용 |
|---|---|
| Runtime | 단일 Develop EC2, SSM, Ingress 0개 SG, 암호화 Volume, IMDSv2와 종료 방지 적용 |
| Registry | FE·BE·AI ECR, Immutable Tag, Push Scan, Lifecycle 정책 적용 |
| CI 인증 | Repository·`develop` Branch별 GitHub OIDC Publisher Role과 최소 Push 권한 적용 |
| CI 검증 | FE Image 1개, BE Main·Payment Image 2개의 Workflow 성공과 ECR 존재 확인 |
| Secret | Develop Runtime Secret과 EC2 Role의 단일 `GetSecretValue` 권한 적용 |
| 배포 | 비민감 `.env`와 임시 Secret env를 결합한 Compose 최초 배포 성공 |
| Runtime 검증 | 네 Container Healthy, Frontend와 Backend Proxy Health 성공 |
| 접근 | Tailscale Serve HTTPS의 `tailnet only`, Mac Curl과 Browser 화면 확인 완료 |
| 보안 보완 | 배포 종료 시 임시 Secret과 ECR Docker Login Token 자동 제거 적용 |

### 검증 결과

- Terraform `fmt`·`validate` 통과.
- Secret Plan `1 added, 1 changed, 0 destroyed` 적용 완료.
- Secret `AWSCURRENT`와 다섯 필수 Key 계약 확인, 실제 값 미출력.
- EC2 Role의 Secret 조회와 ECR Image Pull 계약 확인.
- Docker Compose v5.6.0과 네 Container Health 확인.
- `127.0.0.1:8080` Frontend 및 `/api/actuator/health` 성공.
- Tailnet HTTPS의 동일 Frontend·Backend Proxy 성공.
- Peter의 승인된 장치에서 실제 Frontend 화면 표시 확인.
- Funnel 미사용과 `tailnet only` 상태 확인.

### 남은 문제

- Tailscale 기본 전체 허용 정책 유지 상태. 팀원 초대 전 Develop HTTPS 443 전용 Tag·Grant 적용 필요.
- Docker Compose Plugin 수동 설치 상태. EC2 재생성을 위한 Bootstrap 코드화 필요.
- 이 시점의 수동 SSM 배포 상태는 후속 Develop CD 작업에서 자동화·검증 완료.
- AI Dockerfile·CI·Image 발행·Runtime 계약 미완료.
- Secret 자동 Rotation과 Rotation 후 재배포 자동화 미완료.
- PLG 중앙 관측과 Resource 간섭 검증 미완료.
- `main` Production Image의 ECR 이전 미완료.
- 과거 `CURRENT_STATUS.md` 구간의 AWS Account ID·Resource ID 원문 존재. 외부 공개 전 마스킹 정리 필요.

### 다음 작업

1. FE·BE Trigger 기반 Develop CD Terraform·Runtime·Workflow 적용과 실제 배포 검증 완료.
2. 팀원 초대 전 Tailscale Tag·Grant 정책을 HTTPS 443으로 축소.
3. Docker Compose 설치를 검증 가능한 Bootstrap 단계로 코드화.
4. AI Runtime 계약 확정 후 AI 팀 소유 Dockerfile·CI 연결.
5. Develop PLG 최소 구성과 관측 Stack Resource Budget 검증.

상세 기록: `docs/2026-10-11-v2-develop-runtime-deployment.md`

## 2026-10-11 Develop Runtime 구축과 Tailscale 경로 검증

### 결론

- 기존 `seonjalal-v1-dev`는 V1과 V2의 동일 Image 비교를 위한 Benchmark Server로 보존한다.
- Benchmark의 Test Data는 폐기·재생성 가능한 자료이므로 별도 Snapshot과 Custom AMI를 만들지 않는다.
  대신 API 종료 방지와 Root EBS 보존 설정만 활성화했으며, 이는 Backup을 대체하지 않는다.
- 새 Develop Runtime은 V2 Foundation의 Public Subnet 한 곳에 단일 EC2로 시작한다. Public IPv4는
  사용하지만 Security Group Public Ingress는 0개로 유지하고 SSM과 Tailscale만 접근 경로로 사용한다.
- Develop Runtime용 별도 Terraform Root와 S3 State Key를 적용했다. 신규 EC2 1대와 IAM Role,
  Instance Profile, Public Ingress가 없는 Security Group을 생성했으며 기존 V1 Benchmark에는 변경이 없다.
- Peter의 Mac과 신규 Develop EC2를 같은 Tailnet에 등록하고 MagicDNS 이름을 이용한 Tailscale Ping을
  검증했다. 연결은 DERP 중계로 시작한 뒤 Peer-to-Peer 경로로 전환됐다.

### 완료한 작업

- Develop Runtime 배치·접근·Backup·배포 원칙을 ADR-004에 기록했다.
- Foundation Remote State의 `network` Output을 읽어 Public-a Subnet을 재사용하도록 구성했다.
- Amazon Linux 2023, EC2 IAM Role, SSM, ECR Pull 권한, 암호화된 gp3 Root Volume, IMDSv2,
  종료 방지와 Public Ingress 0개인 Security Group을 코드로 작성했다.
- Bootstrap은 Docker, SSM Agent와 Tailscale Client/Daemon을 준비하되 Tailscale Auth Key나
  Application Secret을 User Data에 넣지 않도록 경계를 정했다.
- `terraform fmt -check -recursive`, Provider 초기화와 `terraform validate`를 통과했다.
- 공개 Output에서 Private IP와 Security Group ID를 제거하고, 운영상 필요한 Instance ID는
  Sensitive Output으로 분리했다.
- 저장 Plan Apply 결과는 `7 added, 0 changed, 0 destroyed`였고 EC2 `running`, IAM Instance Profile 연결,
  API 종료 방지, SSM Agent `Online`을 확인했다.
- SSM Session Manager Plugin을 Mac에 설치하고 SSM Session에서 `cloud-init` 완료, Docker·SSM Agent·
  `tailscaled` 활성화, Bootstrap Version과 표준 Data Directory 권한을 검증했다.
- Tailscale 장치 이름을 `seonjalal-v2-develop-runtime`으로 등록하고 Mac에서 MagicDNS 이름으로
  `tailscale ping`이 성공하는 것을 확인했다. 실제 Application Container와 HTTPS Serve는 아직 없다.
- Tailnet의 Device Approval을 활성화했다. 현재 Peter의 Mac과 Develop EC2만 등록되어 있으며 팀원
  초대 전 Develop HTTPS만 허용하는 Tag·Grant 정책으로 기본 전체 허용 규칙을 교체해야 한다.
- Develop 전용 FE·BE·AI ECR Repository, Push Scan, AES256 암호화, Immutable Tag와 Lifecycle 정책을
  Terraform에 추가하고 `fmt`와 `validate`를 통과했다. 승인된 Plan `6 added, 0 changed, 0 destroyed`를
  적용했다. 실제 ECR 설정과 Apply 직후 무변경 Plan도 검증했다.
- FE·BE·AI 저장소별 Develop ECR Publisher OIDC Role과 최소 Push 권한을 Terraform으로 작성했다.
  세 저장소가 2026-07-15 이후 생성된 것을 공개 GitHub Metadata로 확인해 Owner·Repository ID를 포함한
  불변 OIDC Subject를 사용했다. `develop` Branch의 정확한 Repository Subject만 Role을 수임할 수 있고
  각 Role은 자기 ECR에만 접근한다. 승인된 Plan `6 added, 3 tag-only changed, 0 destroyed`를 적용했으며
  실제 Trust·Inline Policy와 무변경 Plan 검증이 남아 있다.
- FE와 BE의 `develop` Push 검증 성공 후 `develop-<full Git SHA>` Immutable Image를 ECR에 발행하는
  Job을 추가했다. 기존 `main` Docker Hub 발행은 Production ECR·Pull 권한·CD가 준비될 때까지만 임시로
  유지하고, 최종적으로 `main`도 별도 Production ECR로 이전한다. YAML 정적 검사는 통과했으며 OIDC
  Role Apply, GitHub Repository Variable 등록과 실제 Workflow 실행은 아직이다.

### 다음 작업과 중단 조건

1. 팀원 초대 전에 Tailscale Device Approval을 활성화하고 기본 전체 허용 정책을 Develop HTTPS만
   허용하는 Tag·Grant 정책으로 교체한다.
2. FE·BE용 ECR Repository와 Image Tag·Digest·보존 정책을 Terraform으로 구성한다.
3. Develop Docker Compose와 Tailscale Serve를 연결하고 Mac에서 HTTPS Health·Smoke를 검증한다.
4. FE·BE Repository의 `develop` CI가 ECR에 Image를 발행하고, CD가 SSM으로 희망 Digest를 배포하도록
   GitHub OIDC 권한과 Workflow를 구성한다.
5. 팀원을 Tailnet에 초대하고 각 장치를 승인한 뒤 Develop HTTPS 이외의 경로가 차단되는지 검증한다.

## 2026-10-09 A2 실행 기준 고정과 A3 Foundation 구축 완료

### 결론

- A2는 SLI/SLO를 새로 작성하는 작업이 아니다. 기존 잠정 SLO를 유지하고 아직 열려 있던 예산,
  복구 목표, Component 기본 범위, Owner와 기한을 V2 실행 기준으로 고정했다.
- 월 예산은 `300,000원 + 사전 승인된 추가 비용`을 기준으로 한다.
- V1 추가 한계 시험은 V2 Foundation의 선행 조건에서 제외했다. V1 전용 Instance를 보존하고,
  관측 가능한 V2를 구성한 뒤 같은 Image 비교 → Code 최적화 Image 비교 순서로 시험한다.
- A3 Develop Foundation Network Terraform의 최초 NAT 포함 Plan은 폐기했고, NAT 제외 Plan
  `30 to add, 0 to change, 0 to destroy`를 승인 후 적용했다. Apply는 `30 added, 0 changed,
  0 destroyed`로 완료됐다.
- Apply 직후 무변경 Plan, 계층별 Route와 S3 Gateway Endpoint 연결까지 검증해 A3를 완료했다.

### 완료한 작업

| 영역 | 내용 | 근거 |
|---|---|---|
| A2 범위 | Redis·Queue는 기본 Critical Path에서 제외했다. AI 기능은 V2 범위에 포함하되 Runtime 배치와 PostgreSQL/pgvector의 데이터·복구 책임은 별도 결정하도록 경계를 정했다. | `docs/decisions/003-v2-scope-reliability-budget.md` |
| A2 신뢰성 | 기존 rolling 30일 99.5% 잠정 SLO를 유지하고 Production 초기 목표를 RTO 60분·RPO 5분으로 두되 Drill 전에는 달성값으로 표현하지 않도록 했다. | ADR-003, `load-tests/SLI-SLO.md` |
| A2 일정 | A3→A4→B1/B2→A5/A6→B3/B4 Critical Path와 Owner·목표일을 기록했다. | ADR-003 |
| A3 Network | VPC, 2개 AZ의 Public/App/Data Subnet 6개, IGW, Route Table과 S3 Gateway Endpoint를 Module로 작성했다. NAT와 App Internet Default Route는 후속 Egress 결정으로 분리했다. | `infrastructure/terraform/modules/foundation_network/` |
| A3 보안 경계 | Internet→ALB 80/443, ALB→ECS 8080, ECS→RDS 3306 Source SG 규칙을 작성했다. | Foundation Network Module |
| A3 Apply | Develop Foundation 30개 Resource를 생성했고 변경·삭제·부분 실패 없이 완료했다. | Terraform Apply 결과, 2026-10-09 |
| Git 안전 | 실제 Account ID, Profile과 State Bucket을 예시 파일의 Placeholder로 바꾸고 State·Plan·실제 변수·Backend·Credential 관련 Ignore를 보강했다. | `.gitignore`, `*.example` |

### 검증한 항목

- `terraform fmt -check -recursive`: 통과
- `terraform validate`: 경고 없이 `Success! The configuration is valid.`
- S3 Remote Backend 초기화와 AWS Plan까지 수행했다. 최초 Plan은 NAT 포함 기준이어서 폐기하고 NAT 제외
  Code로 `fmt`, `validate`를 다시 통과했다. 새 Plan 검증은 아직이며 Apply와 Resource 변경은 없었다.
- Data Route Table에는 Internet/NAT Default Route Resource를 만들지 않았고 Terraform `check`로
  `0.0.0.0/0` 금지 조건을 추가했다.
- NAT 제외 후 정적 검사에서 Default Route Resource는 Public Route Table 한 개만 남았고
  `aws_eip`, `aws_nat_gateway`, `nat_gateway_id` 참조가 없음을 확인했다.
- 새 AWS Plan에서 `aws_route.default["public"]` 하나만 생성되고 App/Data Route Table에는 Internet
  Default Route가 없으며 V1 Resource의 변경·삭제가 없음을 확인했다.
- 승인된 저장 Plan Apply 결과가 `30 added, 0 changed, 0 destroyed`임을 확인했다. 실제 Resource
  Identifier는 공개 문서에 기록하지 않는다.
- Apply 직후 `No changes`를 확인했고 Public에만 Internet Default Route가 있으며 App은 S3 Endpoint,
  Data는 Local Route만 사용하는 실제 구성을 확인했다.
- S3 Gateway Endpoint는 `available`, `Gateway`, App Route Table 2개 연결 상태다.
- Foundation의 실제 Account ID는 필수 Local 변수로만 받으며 추적 가능한 기본값에서 제거했다.

### 남은 문제와 안전 경계

- ADR-003의 RTO·RPO는 복원 Drill 전 초기 목표이며 팀 승인·달성값이 아니다.
- Redis·Queue 제외 기본안과 AI Runtime·Database 계약은 2026-10-12까지 PL/BE/AI 확인이 필요하다.
- ECS Security Group에는 A4 재검토 대상인 임시 `0.0.0.0/0` Egress 규칙이 있지만 App Route Table에
  Internet Default Route가 없으므로 현재 Foundation만으로 Internet 통신은 성립하지 않는다.
- NAT Gateway·NAT Instance·Interface Endpoint 조합은 AI 외부 API와 ECS/ECR/Logs/Secrets/SSM 요구,
  비용과 운영 책임을 확인한 뒤 별도 Egress Plan으로 결정한다.
- A3 Foundation은 완료됐다. 후속 Workload 배치 전까지 NAT 없는 App Subnet은 S3 이외의 Internet·AWS
  Service 접근이 불가능하다는 경계를 유지한다.

### 다음 작업

1. A4 전에 AI Runtime·Database 계약과 Egress 방식을 별도 ADR로 결정한다.
2. IAM Role, Secret, ECR와 ALB 실행 기반을 별도 Root와 Plan으로 구현한다.
3. ECS Workload 연결 전에 임시 전체 Egress Security Group 규칙을 목적지별로 재검토한다.

## 2026-10-07 통합 현황과 PL 미팅 반영 우선순위

- 기준일: 2026-10-07
- 팀: 취향저격단
- Cloud 담당: Peter 1명
- 현재 단계: V1 실측·문서화와 V2 Terraform 기반 준비 완료, V2 Network·RDS·ECS 구현 착수 전

### 결론

V2의 핵심 성과는 Resource를 많이 만드는 것이 아니라 **V1 MySQL을 RDS로 Data 유실 없이 이전하고,
V1 EC2에서 V2 ECS로 사용자 요청 중단 없이 전환한 과정을 재현 가능한 증거로 남기는 것**이다.

작업은 다음 두 Track으로 나눈다.

| 구분 | 목적 | 진행 원칙 |
|---|---|---|
| 빠르게 해결할 작업 | V2 핵심 작업을 시작하기 위한 선행 조건과 운영 공백 제거 | 이미 결정된 설계대로 최소 운영 가능 수준까지 구현하고 완료 조건을 확인한 뒤 넘어간다. |
| 중요하게 깊게 가져갈 작업 | Portfolio에서 설명할 수 있는 무중단 전환·복구 역량 증명 | 선택 근거, 실패 시나리오, 측정값, Rollback과 재현 절차까지 기록한다. |

Kubernetes 기반 V3 설계는 V2 전환을 막지 않는 별도 Track으로 진행한다. 강의 전체를 먼저 수강하기보다
실제 Cluster와 Workload를 설계하면서 필요한 내용을 선택적으로 학습한다.

### 지금까지 완료한 작업

| 영역 | 현재 상태 | 완료·검증 내용 | 근거 |
|---|---|---|---|
| V1 Production | 완료 | EC2에서 FE·BE·MySQL을 Docker Compose로 운영하고 GitHub OIDC와 SSM을 이용한 수동 승인 CD, Health·Smoke와 Image Rollback 경로를 구성했다. | `docs/ARCHITECTURE.md` |
| V1 Benchmark | 완료 | 기존 V1 Develop을 비교 시험 기준점으로 전환하고 전용 Domain·HTTPS를 구성했다. 지속적 자동 배포 대상은 신규 V2 Develop으로 분리했다. | `docs/PROJECT_CONTEXT.md` |
| 9개 API 부하테스트 | 완료 | Smoke, API별 5 RPS Baseline, 혼합 Baseline, Capacity, Spike, Stress를 수행했다. 30 RPS 20분과 60 RPS Spike는 오류 없이 통과했고 약 189 RPS부터 5초 Timeout이 시작됐다. | `docs/2026-10-02-v1-load-test-final-report.md` |
| V1 병목 분석 | 완료 | Stress에서 단일 EC2 CPU 100%, Backend 최대 120.56%, MySQL 최대 75.32%와 실행 대기 증가를 확인했다. 첫 구조적 한계는 Backend와 MySQL의 Host CPU 경쟁 및 Backend 처리 대기다. | `load-tests/handoff/v1/v1-load-test-final-report.md` |
| SLI·SLO | 설계 완료·운영 검증 전 | 사용자 경계 가용성, 핵심 API 응답시간과 사용자 Journey SLI 및 잠정 SLO를 문서화했다. 부하테스트 결과는 Release Gate 근거이며 월간 SLO 달성 증거로 사용하지 않는다. | `load-tests/SLI-SLO.md` |
| AWS 장애 알림 | 완료 | CloudWatch Alarm 3개의 `ALARM`·`OK` Action을 활성화하고 Discord 실제 수신과 약 1분 뒤 자동 복구 알림을 확인했다. Alerting State도 S3 Backend로 이전하고 무변경 Plan을 검증했다. | `docs/2026-10-07-v1-alerting-activation-and-state-migration.md` |
| Terraform State | 완료 | V2 Bootstrap State 전용 S3 Bucket에 Versioning, AES256, Public Access Block, TLS 강제 Policy와 S3 Native Lock을 적용했다. Local State를 S3 Backend로 이전하고 무변경 Plan까지 검증했다. | `docs/2026-10-06-v2-aws-inventory-and-terraform-bootstrap.md` |
| V2 Compute 결정 | 완료 | Backend Compute를 ECS on EC2, Multi-AZ ASG·Capacity Provider·ALB·Rolling Deployment·Circuit Breaker 구조로 결정했다. 초기 Instance·Task 수는 확정 용량이 아니라 시험 시작값이다. | `docs/decisions/001-v2-infrastructure.md` |
| V2 Network Egress 결정 | 완료 | Develop은 NAT Gateway 1개, Production은 AZ별 NAT Gateway 2개, 공통으로 S3 Gateway Endpoint를 사용하는 안을 확정했다. RDS Data Subnet에는 Internet Default Route를 두지 않는다. | `docs/decisions/002-v2-network-egress.md` |
| Terraform·HCL/Kubernetes 학습 | 진행 중 | Remote State 전환에 필요한 Terraform·HCL을 학습했고 V3 설계를 위해 Kubernetes 기초를 복습하고 있다. 학습 자체를 완료 조건으로 삼지 않고 이후 구현 결과로 검증한다. | 이번 주 작업 및 PL 미팅 기록 |

> 약 189 RPS는 V1의 안전 처리량이나 보장값이 아니라 Stress 시험에서 최초 실패가 관측된 지점이다.
> 현재 증거로 확인한 안전 범위는 혼합 30 RPS 지속과 짧은 60 RPS Spike다.

### Track A — 빠르게 해결할 작업

이미 결정된 구조를 다시 장기간 비교하지 않고 V2 핵심 작업의 선행 조건을 만드는 작업이다.

| 순서 | 작업 | 범위 | 완료 조건 |
|---:|---|---|---|
| A1 | **완료 — V1 장애 알림 마무리** | Discord 실제 수신 확인, 별도 Plan 검토 후 Alarm Action 활성화, Alerting State의 Remote Backend 이전 | 2026-10-07 실제 `ALARM`·`OK` 수신, S3 State와 무변경 Plan까지 확인했다. |
| A2 | **실행 기준 고정·팀 확인 대기** | 기존 잠정 SLO, 월 30만 원+a, RTO·RPO 초기 목표, Component 기본 범위, Owner와 기한을 ADR-003에 기록 | 2026-10-12까지 PL/BE가 Redis·Queue·AI DB 기본 제외안을 확인하면 닫는다. |
| A3 | **IaC 작성·Local 검증 완료, Plan 대기** | VPC, 두 AZ Public/App/Data Subnet, IGW, NAT, Route Table, S3 Gateway Endpoint와 Security Group Module 작성 | `fmt`·`validate` 통과. AWS Plan에서 V1 변경 0건과 Data Route를 확인해야 한다. |
| A4 | V2 실행 기반 | IAM Role, Secret 주입 경계, ECR, ALB·Target Group·ACM·Route 53의 최소 구성을 구현 | Private ECS 경로에서 Image Pull·Secret 조회·Log 전송·RDS 접속이 가능하고 외부 진입점은 ALB 443으로 제한된다. |
| A5 | Develop 최소 CI/CD | `develop` 검증 Image 발행, ECS 배포, Stable 대기, Health·Smoke, 동시 배포 방지와 이전 정상 Revision 복귀 연결 | 수동 서버 작업 없이 Develop 배포가 완료되고 실패 시 이전 정상 Version으로 복구된다. |
| A6 | 최소 관측·알람 | CloudWatch의 ALB 5xx·응답시간, ECS Task·CPU·Memory, RDS CPU·Connection·Latency와 배포 Event부터 수집 | RDS·ECS 전환 시험에서 오류율, p95·p99와 원인 지표를 같은 시간대에 비교할 수 있다. |
| A7 | 운영 보조 Resource | 범위가 확정된 Queue·Redis를 설계값대로 구성하고 Secret·보안 그룹·Backup 기본값 적용 | Application 연결 Smoke와 장애 시 영향 범위를 확인한다. 범위 미확정이면 Critical Path에서 제외한다. |

Prometheus·Grafana·Loki 전체 구축은 A6을 막지 않는다. 먼저 무중단 전환을 판정할 최소 Metric과
Log를 확보하고, 중앙 관측 Stack은 아래 심화 작업의 증거 수집 요구에 맞춰 단계적으로 확장한다.

### Track B — 중요하게 깊게 가져갈 작업

#### B1. MySQL Container → RDS 무중단 Data Migration

단순 Dump·Restore 성공이 아니라 Cutover 중 Data 정합성과 복구 가능성을 증명한다.

- V1 MySQL과 RDS의 Engine Version, Character Set, Collation, Timezone, SQL Mode와 Flyway 상태를 대조한다.
- Data 크기, 쓰기 허용 중단시간과 RPO를 기준으로 Migration 방식을 선택하고 제외한 대안을 기록한다.
- 사전 Migration Rehearsal에서 소요 시간, 행 수, Checksum, 핵심 Query와 API Smoke를 측정한다.
- 신규·기존 Application Version이 동시에 동작할 수 있도록 Schema 변경은
  Expand–Migrate–Contract 순서를 사용한다.
- Cutover 직전·직후의 쓰기 처리 방식과 최종 동기화 절차를 명시한다.
- Rollback 가능 시점, RDS 전환 후 V1 DB에 생기는 쓰기 처리와 Rollback 중 Data 유실 방지 방법을
  Runbook에 기록한다.
- Snapshot 또는 PITR로 별도 RDS를 실제 복원하고 RTO·RPO와 정합성을 측정한다.

완료 조건: 동일한 Rehearsal을 반복할 수 있고, Cutover 전후 Data 정합성·핵심 API·Rollback 판단
결과와 측정 시간이 증거로 남아 있다.

공식 근거: [RDS Backup과 PITR](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_WorkingWithAutomatedBackups.html)

#### B2. V1 EC2 → V2 ECS 무중단 Application 전환

- ECS on EC2의 ASG·Capacity Provider, Task Placement, Service Auto Scaling과 두 단계 Scale-out 시간을
  분리해 측정한다.
- 최소 두 Task를 서로 다른 AZ에 배치하고 ALB Health를 통과한 Task만 Traffic을 받게 한다.
- Readiness, Graceful Shutdown, Deregistration Delay, Stop Timeout과
  `minimumHealthyPercent`·`maximumPercent`를 실제 Rolling 시험으로 보정한다.
- 기동 실패, Readiness 실패와 배포 후 Smoke 실패 Image를 각각 배포해 Circuit Breaker와 이전
  Task Definition Revision Rollback을 검증한다.
- Task Kill, EC2 종료, 한 AZ의 Application Capacity 제거 상황에서 요청 실패율, 복구시간과 Event를
  기록한다.
- 같은 Image·Fixture·k6 Scenario로 V1과 V2의 30·60 RPS를 비교한 뒤 300 RPS Stress와
  600 RPS Burst의 실패·회복 양상을 측정한다.

완료 조건: 배포·Task·Instance 장애 중 정의한 SLO 범위에서 Traffic이 유지되고, 실패 배포가 이전
정상 Revision으로 복구되는 과정과 RTO가 재현 가능하게 기록된다.

공식 근거: [ECS Deployment Circuit Breaker](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/deployment-circuit-breaker.html)

#### B3. DB와 Application을 결합한 Production Cutover

```text
k6 지속 E2E 요청 시작
  → RDS 최종 동기화·정합성 확인
  → ECS 정상 Task와 ALB Health 확인
  → DNS 또는 ALB Traffic을 V2로 전환
  → 오류율·p95·p99·Data 쓰기 결과 확인
  → 성공 조건 충족 시 V2 유지
  → 중단 조건 충족 시 사전 정의한 경로로 Rollback
```

- k6는 전환 전부터 전환 후 안정화 시점까지 중단하지 않고 실행한다.
- Release Evidence에 Git SHA, Image Digest, Task Definition Revision, Migration Version, 전환 시각,
  오류율, p95·p99와 Rollback 여부를 남긴다.
- Application Rollback이 DB Schema·Data를 자동으로 되돌리지 않는 경계를 명시한다.
- DNS TTL, 기존 Connection Drain과 구·신 Version 동시 실행 시간을 기록한다.

완료 조건: 사용자 요청 중단과 Data 유실 여부를 수치로 판정할 수 있고, 성공·중단·Rollback 기준이
실행 전에 정해져 있다.

#### B4. 중앙 관측과 장애 분석

- Prometheus·Grafana·Loki는 설치 자체보다 B1~B3의 원인을 한 시간축에서 설명하는 데 사용한다.
- Backend RED, JVM·GC·Thread·HikariCP, ALB, ECS/ASG, RDS와 배포 Event를 연결한다.
- k6 Test ID, Git SHA와 Image Digest를 Metric·Log에 연결한다.
- Label Cardinality, Log의 Token·Cookie·Password Redaction, 보존기간과 Resource Budget을 먼저 정한다.
- 동일 Host 배치가 부하테스트를 왜곡하는지 측정하고 필요하면 전용 Host 또는 관리형 구성을 비교한다.

완료 조건: k6 Latency 상승 시 ALB → ECS → JVM/HikariCP → RDS → Application Log 순서로 원인을
추적하고 관련 Alert·Recovery·Runbook을 함께 확인할 수 있다.

### Kubernetes V3 설계 Track

약 18개의 업무 도메인을 각각 Repository, Deployment 또는 Worker Node 하나로 대응시키지 않는다.
도메인, 배포 단위와 실행 Node는 서로 다른 결정이다.

초기 설계 후보는 다음과 같이 관련 책임을 3~4개 Workload Group으로 묶고, 이후 독립 배포·확장·장애
격리 요구가 있는 Component만 별도 Deployment로 분리하는 방식이다.

| Workload Group 후보 | 포함 기능 예시 | 분리 검토 기준 |
|---|---|---|
| 인증·사용자 | Auth, User, Profile, Friend | 보안 경계, 인증 Traffic, 독립 배포 필요성 |
| 상품·추천 | Product, Category, Recommendation | CPU/GPU 요구, 추천 Traffic, AI 의존성 |
| 선물·주문 | Gift, Order와 관련 쓰기 흐름 | Data 정합성, Transaction 경계, 쓰기 확장 |
| 비동기·운영 | Notification, Queue Consumer, Batch | 실행 주기, 실패 재처리, 독립 Scaling |

- 외부 진입은 우선 `Ingress 또는 AWS Load Balancer Controller → ClusterIP Service → Pod` 구조를
  검토한다.
- NodePort를 Internet에 직접 노출하는 안은 초기 후보에서 제외한다.
- Pod 배치는 AZ 장애 분산, Resource Request·Limit, Affinity·Anti-affinity, Topology Spread와
  장애 격리를 기준으로 결정한다.
- Worker Node 수는 도메인 수가 아니라 Pod Resource 요구량, 최소 가용성, 장애 여유와 비용으로
  산정한다.

완료 조건: 각 Deployment의 분리 이유, Service 노출 방식, Pod 배치, Node 용량, 장애 범위와 비용을
다른 사람이 문서만 읽고 검토할 수 있다.

### 권장 실행 순서

```text
A1 알림 마무리 + A2 범위·일정 고정
  → A3 Network IaC
  → A4 IAM·ECR·ALB·DNS 실행 기반
  → B1 RDS Migration Rehearsal 시작
  → B2 ECS Runtime·Rollback 시험 시작
  → A5 Develop CI/CD 연결
  → A6 최소 관측 보강
  → B3 통합 Cutover Rehearsal·Production 전환
  → B4 중앙 관측 고도화
  → Kubernetes V3 설계 구체화
```

짧은 작업은 먼저 닫되, B1과 B2는 모든 보조 작업이 끝날 때까지 기다리지 않고 필요한 기반이
준비되는 즉시 시작한다. Cloud 담당자가 한 명이므로 각 단계는 `Plan/사전 점검 → 실행 → 검증 →
Rollback 가능 여부 확인 → 증거 저장`을 완료한 뒤 다음 Production 영향 단계로 이동한다.

### 현재 남은 결정과 위험

- RDS Migration 중 허용 가능한 쓰기 중단시간, RPO와 Rollback 가능 시점이 확정되지 않았다.
- Redis·Queue·AI PostgreSQL/pgvector가 V2 필수 범위인지 팀 합의가 필요하다.
- V2 월 예산, RTO·RPO와 잠정 SLO 승인값이 열려 있다.
- Prometheus·Grafana·Loki의 배치 위치와 Resource Budget이 정해지지 않았다.
- Foundation VPC Terraform Module, ECR, RDS, ALB, ECS와 V2 CI/CD는 아직 구현하지 않았다.
- 18개 도메인의 경계만으로 Deployment·Pod·Node 수를 확정할 수 없다.

### 다음 작업

1. RTO·RPO·예산과 V2 포함 Component를 팀과 확정해 Critical Path를 고정한다.
2. Foundation VPC Terraform Module과 Data Subnet Route Check를 작성한다.
3. Network 정적 검증 후 Develop Plan에서 V1 Resource 변경이 0인지 확인한다.
4. RDS Migration Runbook 초안과 첫 Rehearsal Data 검증 항목을 작성한다.
5. ECS 최소 Service와 잘못된 Image Rollback 시험 시나리오를 구체화한다.

## 2026-10-07 V1 장애 알림 활성화와 State Migration 완료

### 완료한 작업

- SNS 수동 발행 메시지가 Discord `infra_error`에 도착하는 것을 확인했다.
- Production CloudWatch Alarm 3개의 `alarm_actions`와 `ok_actions`를 기존 SNS Topic에 연결했다.
- CPU High Alarm을 `TEST` 상태로 전환해 실제 `ALARM` 메시지를 확인했고, 실제 Metric 재평가로 약
  1분 뒤 `OK` 복구 메시지가 도착하는 것을 확인했다.
- Alerting Terraform에 S3 Backend 선언을 추가하고 Local State를 공용 State Bucket의
  `seonjalal/production/alerting/terraform.tfstate`로 이전했다.
- 실제 `backend.hcl`, State, Plan과 `terraform.tfvars`는 Git에서 제외하고 Local 파일 권한을
  `600`으로 제한했다.

### 검증한 항목

- 활성화 Plan은 `0 add / 3 change / 0 destroy`였고 세 CloudWatch Alarm의 `alarm_actions`와
  `ok_actions`만 in-place로 변경했다.
- Apply 결과도 `0 added / 3 changed / 0 destroyed`였으며 Application과 EC2 재시작은 없었다.
- 세 Alarm 모두 `ActionsEnabled=True`, ALARM Action 1개, OK Action 1개와 최종 상태 `OK`를 확인했다.
- Remote State Object는 `application/json`, `AES256`, 29,236 Byte와 S3 Version ID를 가졌다.
- Migration 종료 후 현재 Prefix에는 State Object만 있고 활성 `.tflock`은 남지 않았다.
- S3 Backend에서 기존과 같은 State 주소 12개를 읽었고 최종 Plan은 `No changes`였다.

### 트러블슈팅과 안전 조치

- 기본 AWS Profile이 다른 계정을 가리키는 것을 `sts get-caller-identity`로 발견해 Apply 전에
  중단하고 `seonjalal-production` Profile로 전환했다.
- 만료된 Login Session을 갱신하는 과정에서 브라우저의 root Console Session 때문에 Profile이
  root 자격 증명으로 한 차례 연결됐다. Root로는 `get-caller-identity`만 수행하고 Resource 변경 없이
  즉시 `aws logout`한 뒤 `peter.jung-infra` IAM 사용자로 재인증했다.
- `terraform fmt -check`가 파일명을 출력한 것을 성공으로 오인할 수 있어 Migration 전에 중단하고
  `terraform fmt` 후 무출력 통과를 다시 확인했다.
- Migration Prompt에 한글 입력 흔적이 있었지만 최종 `yes`가 인식됐고 S3 Backend 구성·초기화 성공,
  Remote Object와 무변경 Plan으로 State 복사를 재검증했다.
- 화면 공유에는 Account ID, Instance ID와 일회성 인증 URL이 노출될 수 있어 Issue·PR 증거에서는
  마스킹하고 Webhook, Token, Cookie와 State 원문은 공유하지 않는다.

### 남은 문제

- Migration 전 Local State 원본과 Backup은 권한 `600`으로 단기 보존 중이며 보존기간 확정 후
  안전하게 정리해야 한다.
- Alarm 임계값은 초기 운영값이므로 실제 오탐·미탐과 운영 Baseline을 수집해 보정해야 한다.
- Memory·Disk, 외부 HTTPS, Docker Health와 Lambda 자체 오류 Alert는 후속 범위다.

### 다음 작업

1. V2 RTO·RPO·예산과 Redis·Queue·AI Database 포함 범위를 팀과 확정한다.
2. Foundation VPC Terraform Module과 Data Subnet Route Check를 작성한다.
3. V2 Network Plan에서 V1 Resource 변경이 0인지 확인한다.

상세 작업 과정과 재현·복구 절차:
`docs/2026-10-07-v1-alerting-activation-and-state-migration.md`

## 2026-10-06 V2 Network Egress ADR 확정

### 완료한 작업

- Private ECS Host·Task의 ECR, S3, Logs, Secret과 외부 AI API 접근 요구를 분리했다.
- NAT Gateway, NAT Instance, Public IP와 Interface Endpoint 대안을 비용·가용성·운영 책임으로
  비교했다.
- Develop은 NAT Gateway 1개와 S3 Gateway Endpoint, Production은 AZ별 NAT Gateway 2개와 S3
  Gateway Endpoint를 사용하는 초기 결정을 ADR-002에 기록했다.
- RDS Data Subnet에는 Internet Default Route를 만들지 않는 경계를 확정했다.

### 검증한 항목

- 외부 AI API 요구가 있어 Interface Endpoint만으로 Internet Egress를 제거할 수 없다.
- Develop NAT 1개는 비용 절충이며 Production Multi-AZ 증거로 사용할 수 없다.
- ECR Private Image Pull에는 ECR API/DKR뿐 아니라 S3 Image Layer 경로도 필요하다.
- NAT Instance는 Patch·Scale·장애 Route 전환 책임이 추가돼 초기안에서 제외했다.

### 남은 문제

- 서울 Region의 실제 NAT·Endpoint·전송·Log 비용은 Apply 직전 공식 Calculator 입력으로 확정해야 한다.
- VPC Flow Logs의 목적지와 보존기간, Interface Endpoint 초기 포함 범위가 남아 있다.
- Foundation VPC Terraform Module은 아직 작성하지 않았다.

### 다음 작업

1. VPC·Subnet·Internet Gateway·Route Table·NAT Gateway·S3 Endpoint Module을 작성한다.
2. Data Subnet에 Internet Default Route가 생기지 않도록 Terraform Check를 추가한다.
3. 정적 검증 후 Foundation Plan을 사용자 실행 방식으로 검토한다.

## 2026-10-06 V2 Terraform State Bootstrap 완료

### 완료한 작업

- 사용자가 S3 Remote Backend 기준 `terraform plan -detailed-exitcode`를 실행했다.
- Terraform이 Account `721744297924`의 실제 State Bucket과 보호 설정을 Refresh했다.
- 코드, Remote State와 실제 AWS가 일치해 `No changes`로 Bootstrap 최종 Gate를 통과했다.

### 검증한 항목

- 변경 예정 Resource가 없으며 Add·Update·Replace·Destroy가 모두 0이다.
- S3 State Object, Versioning, AES256, Public Access Block, BucketOwnerEnforced, TLS 강제 Policy와
  S3 Native Lock의 생성·해제를 모두 검증했다.
- V1 Resource는 Bootstrap State에 없고 변경되지 않았다.
- Bootstrap의 Plan→Apply→보호 설정 확인→State Migration→Remote 무변경 Plan 전체 절차가 완료됐다.

### 남은 문제

- Migration 전 Local State 원본과 Backup은 권한 `600`으로 단기 보존 중이며 즉시 삭제하지 않는다.
- Git에서 제외된 `backend.hcl`과 Local State 파일을 Backup·보존 정책에 맞춰 후속 정리해야 한다.
- V2 실제 Network·IAM·ECR Resource는 아직 생성하지 않았다.

### 다음 작업

1. Network Egress의 NAT Gateway·NAT Instance·VPC Endpoint 조합을 비용·가용성·운영 난이도로 비교한다.
2. Develop과 Production의 Egress 방식을 ADR로 결정한다.
3. 결정 후 Foundation VPC Module을 작성하고 정적 검증·Plan까지 진행한다.

## 2026-10-06 Bootstrap S3 Remote State 검증 통과

### 완료한 작업

- 사용자가 S3 Backend 전환 후 Terraform `state list`와 `output`을 다시 실행했다.
- S3 `head-object`, Object Version 목록과 현재 Object Key를 읽기 전용으로 검증했다.
- Remote State Object의 크기, 암호화, Content Type, Version ID와 최신 여부를 확인했다.

### 검증한 항목

- Terraform이 Remote State에서 Data Source 2개와 Managed Resource 6개를 동일하게 읽는다.
- Remote State 크기 13,951 Byte는 Migration 전 Local State·Backup 크기와 일치한다.
- State Object는 `AES256`, `application/json`, 최신 Version
  `rY7M8AR7mrszFsT6b5vHpb_OCX2.6Hvy`로 저장됐다.
- `.tflock`의 과거 Version과 최신 Delete Marker는 Migration 중 Lock 생성·정상 해제 기록이다.
- 현재 Object 목록에는 `bootstrap/state/terraform.tfstate`만 있어 활성 Lock이 남아 있지 않다.
- Delete Marker는 State가 아니라 과거 Lock Object에만 있으며 State Object는 삭제되지 않았다.

### 남은 문제

- Remote Backend를 기준으로 코드·State·AWS가 일치하는지 `No changes` Plan을 확인해야 한다.
- 검증 전까지 Local State 원본과 Backup은 보존한다.
- Bootstrap 완료 후 다음 우선 작업인 Network Egress ADR과 Foundation VPC Module이 남아 있다.

### 다음 작업

1. 사용자가 Remote Backend 기준 Terraform Plan을 실행한다.
2. `No changes`와 정상 Lock 해제를 확인한다.
3. 통과 후 Bootstrap을 완료 처리하고 Local State 사본 정리 방법을 결정한다.

## 2026-10-06 Bootstrap Local State의 S3 Backend Migration 성공

### 완료한 작업

- 사용자가 `terraform init -migrate-state -backend-config=backend.hcl`을 직접 실행했다.
- Terraform이 기존 `local` Backend와 새 `s3` Backend를 감지하고 기존 State 복사 승인을 요청했다.
- 사용자 승인 후 Local State를 S3 Backend로 복사하고 Backend 초기화를 완료했다.
- AWS Provider `6.67.0`과 기존 Dependency Lock File을 재사용했다.

### 검증한 항목

- Terraform 출력에서 `Successfully configured the backend "s3"`를 확인했다.
- 이어서 `Terraform has been successfully initialized`가 출력돼 Backend와 Provider 초기화가
  정상 종료됐다.
- 새 S3 Backend에는 기존 State가 없었고 Local State를 새 Remote State로 복사한 Migration이다.
- 입력 줄에 한글 입력 흔적이 있었지만 최종 성공 메시지로 State Copy 승인과 초기화 완료를 확인했다.

### 남은 문제

- S3의 `bootstrap/state/terraform.tfstate` Object 존재, 암호화, Version ID와 크기를 확인해야 한다.
- 현재 Terraform 명령이 Remote State에서 동일한 Resource·Output을 읽는지 검증해야 한다.
- Remote State 검증과 무변경 Plan 전에는 Local 원본·Backup을 삭제하지 않는다.

### 다음 작업

1. 사용자가 Terraform `state list`와 `output`으로 Remote State를 읽는다.
2. 사용자가 S3 State Object의 Metadata와 Version 목록을 확인한다.
3. 검증 통과 후 Remote Backend 기준 `terraform plan`이 `No changes`인지 확인한다.

## 2026-10-06 Bootstrap Local State Backup 검증 완료

### 완료한 작업

- 사용자가 Local `bootstrap.tfstate`를 `bootstrap.tfstate.pre-s3-migration`으로 복사했다.
- 원본과 Backup의 권한을 현재 사용자만 읽고 쓸 수 있는 `600`으로 제한했다.
- 두 파일의 SHA-256과 파일 크기를 대조했다.

### 검증한 항목

- 원본과 Backup SHA-256은 모두
  `13be7f7eb36ac5732d57bdaee84decbc1e592a8b77442df1d8152f9acae6451a`로 일치한다.
- 두 파일 크기는 모두 13,951 Byte다.
- 두 파일 권한은 모두 `-rw-------`다.
- `ls -l`의 `@`는 macOS 확장 속성 표시이며 State 권한 실패가 아니다.

### 남은 문제

- Terraform은 아직 Local Backend를 사용하고 실제 State는 Mac 원본 파일에 있다.
- S3 Backend Migration과 Remote Object·Version 검증이 남아 있다.

### 다음 작업

1. 사용자가 `terraform init -migrate-state`를 실행한다.
2. State 복사 확인 Prompt에 `yes`를 입력한다.
3. 성공 후 Local Backup을 보존한 채 Remote State를 검증한다.

## 2026-10-06 State Bucket 보호 검증·S3 Backend 이전 준비 완료

### 완료한 작업

- 사용자가 Terraform Local State에서 Data Source 2개와 S3 Managed Resource 6개를 확인했다.
- Terraform Output의 Account, Bucket 이름과 Backend Key가 설계값과 일치함을 확인했다.
- S3 Region, Public Access Block, Versioning, Encryption, Ownership, Policy Status와 TLS 강제 Policy를
  AWS API로 읽기 전용 검증했다.
- Bootstrap Backend 선언을 Local에서 S3로 변경하고 Git 제외 `backend.hcl`을 준비했다.

### 검증한 항목

- Bucket Region은 `ap-northeast-2`다.
- Public Access Block 네 항목은 모두 `true`다.
- Versioning은 `Enabled`, 기본 암호화는 `AES256`이다.
- Object Ownership은 `BucketOwnerEnforced`, Policy Status는 `IsPublic=false`다.
- Bucket Policy는 `aws:SecureTransport=false` 요청을 `Deny`한다.
- `Contents`와 Key가 없어 State 이전 전 빈 Bucket 상태다. 출력의 `KeyCount=null`은 현재 응답에서
  KeyCount/Contents가 생략된 것으로 Object 존재 증거가 아니다.

### 남은 문제

- 실제 State는 아직 Mac의 `bootstrap.tfstate`에 있다.
- `terraform init -migrate-state`로 Local State를 S3 Key로 복사해야 한다.
- 이전 후 S3 Object, Version ID, State 목록과 `No changes` Plan을 검증해야 한다.

### 다음 작업

1. 사용자가 Local State Backup 사본을 만든다.
2. 사용자가 `init -migrate-state`로 State를 S3 Backend에 이전한다.
3. 이전 성공 후 Local Backup을 삭제하지 않은 채 Remote State와 무변경 Plan을 확인한다.

## 2026-10-06 V2 Terraform State S3 Bucket 최초 Apply 완료

### 완료한 작업

- 사용자가 검토한 `bootstrap.tfplan`을 Mac에서 직접 Apply했다.
- State 전용 S3 Bucket `seonjalal-terraform-state-721744297924-ap-northeast-2`를 생성했다.
- Ownership Controls, Public Access Block, TLS 강제 Bucket Policy, SSE-S3와 Versioning 설정을
  함께 생성했다.
- Apply 결과는 `6 added / 0 changed / 0 destroyed`다.

### 검증한 항목

- 기존 AWS Resource의 Update와 Destroy는 없다.
- State Bucket 생성 후 다섯 보호 설정이 모두 Terraform 오류 없이 완료됐다.
- V1 EC2·VPC·Security Group·상품 Bucket·Alerting Resource는 변경하지 않았다.

### 남은 문제

- 현재 Bootstrap State는 아직 Mac의 `bootstrap.tfstate` Local State다.
- S3 Public 차단, Versioning, Encryption, Ownership, Policy와 Region을 AWS API로 읽기 전용 검증해야
  한다.
- S3 Bucket 보호 검증 후 Local State를 `bootstrap/state/terraform.tfstate` Key로 이전해야 한다.

### 다음 작업

1. 사용자가 Terraform State 목록과 Output을 확인한다.
2. 사용자가 S3 보호 설정과 Bucket 비공개 상태를 읽기 전용으로 확인한다.
3. 검증 통과 후 Backend를 Local에서 S3로 바꾸고 `init -migrate-state`를 실행한다.

## 2026-10-06 사용자 저장 Bootstrap Plan 최종 검토 통과

### 완료한 작업

- 사용자가 정확한 Bootstrap Root Module에서 `bootstrap.tfplan`을 생성했다.
- 사용자가 저장 Plan을 다시 읽어 Resource Action과 최종 변경 수를 확인했다.
- Plan의 `6 add / 0 change / 0 destroy`와 생성 대상 6개가 코드·사전 Plan과 일치함을 확인했다.

### 검증한 항목

- `data.aws_iam_policy_document.terraform_state`는 Bucket Policy JSON을 계산하는 Data Source이고 별도
  AWS Resource 생성 대상이 아니다.
- 실제 생성 대상은 State S3 Bucket 1개와 Ownership, Public Access Block, TLS Policy, Encryption,
  Versioning 설정 5개다.
- 기존 V1 EC2·VPC·Security Group·상품 Bucket·Alerting Resource는 Plan에 없다.
- Change와 Destroy가 0이므로 기존 AWS Resource에 대한 변경 계획이 없다.

### 남은 문제

- 저장 Plan은 아직 Apply하지 않아 State Bucket과 Local Bootstrap State가 없다.
- Apply 성공 후 Bucket 보호 설정과 State 목록을 읽기 전용으로 검증해야 한다.
- 검증 뒤 Local State를 S3 Remote Backend로 이전해야 한다.

### 다음 작업

1. 사용자가 검토한 `bootstrap.tfplan`을 직접 Apply한다.
2. 실패 시 재실행하거나 수동 삭제하지 않고 오류 출력을 먼저 검토한다.
3. 성공 시 `6 added / 0 changed / 0 destroyed`와 Output을 확인한다.

## 2026-10-06 Terraform Plan 실행 위치 오류 차단

### 완료한 작업

- 사용자가 `infrastructure/terraform` 상위 Directory에서 Bootstrap Plan 명령을 실행했다.
- 해당 Directory는 `aws_profile` 변수를 선언한 Root Module이 아니어서 Terraform이
  `Value for undeclared variable`로 Plan을 중단했다.
- 오류가 코드·AWS 권한 문제가 아니라 실행 위치 문제임을 확인했다.

### 검증한 항목

- 오류는 Plan 계산과 AWS 변경 전에 발생했다.
- State Bucket, Plan File과 Terraform State는 생성되지 않았다.
- 올바른 실행 위치는 `infrastructure/terraform/bootstrap/state`다.

### 남은 문제

- 사용자가 정확한 Bootstrap Root Module로 이동해 `pwd`와 `.tf` 파일 존재를 확인해야 한다.
- 위치 확인 후 `fmt`, `validate`, 저장 Plan 생성을 다시 실행해야 한다.

### 다음 작업

1. 사용자가 Mac에서 정확한 Bootstrap Directory로 이동한다.
2. `pwd`와 `variables.tf` 존재를 확인한다.
3. 같은 Directory에서 Plan을 다시 생성한다.

## 2026-10-06 Terraform Apply 대상 AWS Identity Gate 통과

### 완료한 작업

- 사용자가 `--profile seonjalal-production --region ap-northeast-2`를 명시해 STS Identity를 다시
  조회했다.
- Account `721744297924`, IAM User `peter.jung-infra`를 확인해 Bootstrap Plan 대상 Identity Gate를
  통과했다.
- 문서에서 필수로 정의한 `CostCenter=ktb4-12th`가 Plan에서 빠지지 않도록 Bootstrap과 Develop
  Foundation의 공통 Terraform Tag에 고정했다.

### 검증한 항목

- 개인 `default` Profile Account `155359186350`이 아니라 V2 대상 Account로 전환됐다.
- STS 조회는 읽기 전용이며 AWS Resource 변경이 없다.
- Terraform Provider의 `allowed_account_ids`와 명령의 명시적 Profile이 이중으로 Account를 제한한다.

### 남은 문제

- Tag 코드가 바뀌었으므로 이전 화면 Plan은 Apply 근거로 사용할 수 없다.
- 사용자가 새 Plan을 `bootstrap.tfplan`에 저장하고 변경 수와 Resource 목록을 다시 확인해야 한다.

### 다음 작업

1. 사용자가 Mac의 Bootstrap Directory에서 저장 Plan을 생성한다.
2. `6 add / 0 change / 0 destroy`와 State Bucket 관련 Resource만 있는지 확인한다.
3. 검토가 끝난 동일 Plan File만 Apply한다.

## 2026-10-06 Terraform Apply 전 AWS Profile 불일치 차단

### 완료한 작업

- 사용자가 Mac의 Bootstrap Directory에서 `aws sts get-caller-identity`를 직접 실행했다.
- 명령에 `--profile seonjalal-production`이 빠져 Mac의 `default` Profile이 사용된 것을 확인했다.
- 출력 Account `155359186350`, IAM User `peter.jung`은 V2 대상 Account가 아니므로 Plan·Apply를
  진행하지 않고 즉시 중단했다.

### 검증한 항목

- V2 대상 Account는 `721744297924`, IAM User는 `peter.jung-infra`다.
- 실행된 명령은 STS Identity 읽기 전용 조회이므로 두 AWS Account 모두 Resource 변경이 없다.
- Terraform Provider에는 `allowed_account_ids=[721744297924]`가 있어 잘못된 Account로 Terraform을
  실행할 경우에도 Provider 단계에서 차단하도록 구성돼 있다.

### 남은 문제

- `seonjalal-production` Profile의 인증 유효 여부를 다시 확인해야 한다.
- 올바른 Identity 확인 전에는 Bootstrap Plan File 생성과 Apply를 실행하지 않는다.

### 다음 작업

1. 사용자가 `--profile seonjalal-production --region ap-northeast-2`를 명시해 STS Identity를 조회한다.
2. Account `721744297924`, ARN의 `peter.jung-infra`를 확인한 뒤 Plan 단계로 진행한다.

## 2026-10-06 사용자 직접 명령 실행 규칙 추가

### 완료한 작업

- 실제 환경에 영향을 줄 수 있는 AWS, Terraform, 배포, EC2와 Database 명령은 Codex가 대신 실행하지
  않고 사용자가 직접 입력하도록 `AGENTS.md`에 규칙을 추가했다.
- 모든 안내에 실행 위치, 목적, 읽기/변경 범위, 예상 출력, 중단 기준과 복구 방법을 포함하도록 했다.
- Plan→Apply→검증→State 이전을 한 번에 묶지 않고 사용자 실행 결과를 확인한 뒤 다음 명령으로
  진행하도록 했다.
- 실제 `backend.hcl`은 환경별 State 위치와 Local Profile을 포함하므로 Git에서 제외하고
  `backend.hcl.example`만 관리하도록 `.gitignore`를 보강했다.

### 검증한 항목

- State Bootstrap Plan은 이미 `6 add / 0 change / 0 destroy`로 통과했다.
- 아직 Plan File은 저장하지 않았고 State Bucket Apply도 수행하지 않았다.
- 이번 변경은 Local 문서와 Git Ignore 규칙만 수정했으며 AWS Resource 변경은 없다.

### 남은 문제

- 사용자가 Mac에서 새 Plan을 `bootstrap.tfplan`로 저장하고 동일 Plan을 직접 Apply해야 한다.
- Apply 성공 후 Bucket 보호 설정 검증과 Local State의 S3 이전이 남는다.

### 다음 작업

1. 사용자가 AWS Identity를 확인한다.
2. 사용자가 저장 Plan을 만들고 `6 add / 0 change / 0 destroy`를 확인한다.
3. 사용자가 승인된 Plan File을 직접 Apply한다.
4. 실행 결과를 확인한 뒤 별도 명령으로 Bucket 보호 설정을 검증한다.

## 2026-10-06 V2 Terraform State Bootstrap Plan 통과

### 완료한 작업

- Bootstrap Local State에서 AWS Account `721744297924`를 읽고 `terraform plan`을 실행했다.
- Plan File은 저장하지 않았고 `apply`도 실행하지 않았다.
- Plan의 모든 Resource Action을 검토해 State S3 Bucket과 보호 설정만 생성 대상임을 확인했다.

### 검증한 항목

- 결과는 `6 add / 0 change / 0 destroy`다.
- 생성 예정 항목은 S3 Bucket, Ownership Controls, Public Access Block, Versioning, SSE-S3와 TLS 강제
  Bucket Policy다.
- V1 EC2, 기본 VPC·Subnet, V1 Security Group, 상품 Image Bucket, Alarm·SNS·Lambda는 Plan에 없다.
- `-detailed-exitcode`의 종료코드 2는 오류가 아니라 변경 예정 항목이 있다는 정상 결과다.
- `-out`을 사용하지 않아 실행 가능한 Plan File이나 민감 정보가 담긴 새 Artifact를 만들지 않았다.

### 남은 문제

- State Bucket은 아직 생성되지 않았고 AWS 변경은 없다.
- Bucket 이름의 전역 사용 가능 여부는 Apply의 S3 생성 API에서 최종 확인된다.
- 최초 Apply 뒤 Local Bootstrap State를 S3로 이전해야 Remote State 구성이 완료된다.

### 다음 작업

1. State Bucket 1개와 보호 설정 5개의 실제 생성을 사용자에게 설명하고 Apply 승인을 받는다.
2. 승인 후 동일 코드로 새 Plan을 다시 확인하고 Apply한다.
3. Public Access Block, Versioning, Encryption, TLS Policy를 읽기 전용으로 검증한다.
4. Local Bootstrap State를 `bootstrap/state/terraform.tfstate`로 이전한다.

## 2026-10-06 Terraform 용어·State 경계 문서화

### 완료한 작업

- `init`, `validate`, `plan`, `apply`, State, Backend, State Lock, Provider Lock File, Resource,
  Data Source, Import, Drift, Refresh와 Replace를 쉬운 용어로 정리했다.
- Alerting·Bootstrap·Foundation·Data·Platform·Observability State가 서로 다른 제품이 아니라 관리
  범위가 다른 별도 Terraform 장부라는 점을 문서화했다.
- Bootstrap이 VPC 초기화가 아니라 Remote State S3 Bucket을 먼저 만드는 닭과 달걀 문제 해결
  단계임을 실행 순서와 함께 설명했다.
- Plan의 검토 순서와 `add/change/replace/destroy` 중단 기준을 정의했다.
- 상세 용어집을 `docs/TERRAFORM_GLOSSARY_AND_WORKFLOW.md`에 작성했다.

### 검증한 항목

- V1 Alerting State는 Alarm·SNS·Lambda 관련 Managed Resource 11개만 관리하고 V1 EC2·VPC·Subnet을
  관리하지 않는다.
- Bootstrap State와 V2 State에는 V1 EC2·VPC·Security Group이 Import되지 않았고 코드에도 없다.
- S3 Bucket 이름의 전역 중복 여부는 Plan이 아니라 실제 생성 API를 호출하는 Apply에서 최종
  판정된다는 점으로 기존 문서를 정정했다.

### 남은 문제

- Bootstrap Plan은 이후 `V2 Terraform State Bootstrap Plan 통과` 작업에서 완료됐다.
- 용어집과 `CURRENT_STATUS.md`는 현재 `.gitignore` 정책상 Local 문서다.

### 다음 작업

1. Plan 결과를 설명하고 State Bucket Apply 여부는 별도 승인을 받는다.

## 2026-10-06 V2 Terraform Bootstrap 정적 검증 완료

### 완료한 작업

- Bootstrap과 Develop Foundation에 `terraform fmt`를 적용하고 재검사했다.
- 외부 Provider 다운로드 승인 없이 기존 Alerting Terraform의 AWS Provider `6.67.0`을 Local Mirror로
  재사용해 Bootstrap과 Foundation을 초기화했다.
- 두 Root에서 `terraform validate`가 통과했다.
- Local Mirror 초기화로 macOS Checksum만 생성된 두 Lock File에 기존 검증된 Registry Checksum을
  반영해 Linux CI 설치 검증에 필요한 Hash를 보완했다.

### 검증한 항목

- Bootstrap은 S3 Bucket, Ownership, Public Access Block, Versioning, SSE와 TLS 강제 Policy만
  선언하며 V1 EC2·Security Group·S3 상품 Bucket을 참조하지 않는다.
- Foundation은 아직 실제 AWS Resource가 없는 환경 경계 Root이며 Account, Region, VPC CIDR,
  AZ·Subnet CIDR, 이름과 Tag만 출력한다.
- Terraform Provider 실행에는 Sandbox 밖 실행이 필요했지만 Network Download와 AWS 변경은 없었다.

### 남은 문제

- Bootstrap Plan은 이후 재실행해 `6 add / 0 change / 0 destroy`로 검증했다.
- State Bucket 생성 예정 이름과 AWS Provider Read 결과는 Plan 승인 후 확인해야 한다. 이름의 전역
  사용 가능 여부는 Apply에서 최종 확인된다.
- State Bucket Apply와 Remote State 이전은 수행하지 않았다.

### 다음 작업

1. Apply는 Plan·비용·영향을 제시하고 별도 명시적 승인을 받은 뒤 수행한다.
2. 승인 전에는 Network Egress·IAM·ECR ADR처럼 AWS를 변경하지 않는 설계를 진행한다.

## 2026-10-06 V2 AWS Inventory·Terraform Bootstrap 골격 완료

### 완료한 작업

- 만료된 `seonjalal-production` AWS CLI 임시 세션을 `aws login`으로 갱신하고 Account
  `721744297924`, IAM User `peter.jung-infra`, Region `ap-northeast-2`를 재확인했다.
- VPC, Subnet, EC2, Security Group, ECS, ECR, RDS, ALB, ASG, NAT Gateway, VPC Endpoint, S3,
  Route 53, ACM, IAM/OIDC, Secret 메타데이터, CloudWatch Alarm과 AWS Budget을 읽기 전용으로 조사했다.
- V2 Develop을 기본 VPC와 분리한 `10.20.0.0/16`, `ap-northeast-2a/2c`의 Public/App/Data Subnet으로
  구성하는 경계를 정했다.
- Resource Prefix `seonjalal-v2-develop`, 필수 Tag와 Bootstrap/Foundation State Key를 정의했다.
- State S3 Bucket Bootstrap Terraform과 Develop Foundation의 Provider·Backend·변수·경계 Root를
  `infrastructure/terraform/` 아래에 작성했다.
- 상세 결과와 Apply 전 Gate를 `docs/2026-10-06-v2-aws-inventory-and-terraform-bootstrap.md`에 기록했다.

### 검증한 항목

- 현재 계정에는 기본 VPC 한 개와 V1 EC2 두 대가 있고 ECS Cluster, ECR, RDS, ALB, ASG, NAT Gateway,
  VPC Endpoint는 없다.
- S3에는 Develop 상품 Image Bucket 한 개만 있어 Terraform State와 Frontend용으로 재사용하지 않는다.
- `seonjalal.com` Hosted Zone과 GitHub OIDC Provider는 존재하지만 ACM 인증서와 AWS Budget은 없다.
- V1 Production EC2는 실행 중이고 V1 Develop EC2는 중지 상태이며 V2 Terraform State의 관리 대상에
  포함하지 않았다.
- Secret 값, Access Key와 Password는 조회하지 않았고 AWS Resource 생성·수정·삭제는 수행하지 않았다.

### 남은 문제

- Bootstrap·Foundation의 `fmt`, `init`, `validate`와 Bootstrap Plan은 이후 작업에서 완료했다.
- State Bucket이 아직 없으므로 Bootstrap은 Local State이고 Develop Foundation Remote Backend는
  연결할 수 없다.
- NAT Gateway·NAT Instance·VPC Endpoint 조합, AWS Budget 금액과 Terraform Bootstrap Apply 주체가
  미정이다.
- 현재 `.gitignore`는 `docs/`와 모든 Markdown을 제외하므로 상태·설계 문서는 Local에만 존재한다.

### 다음 작업

1. State Bucket 실제 Apply는 Plan·비용·영향을 제시하고 사용자 승인 후 수행한다.
2. Network Egress·비용 ADR과 Foundation VPC Module을 작성한다.

## 2026-10-06 V2 Compute ADR 정합화

### 완료한 작업

- 4단계 Wiki의 V2 결론을 Cloud 저장소의 ADR-001에 반영해 ECS on EC2 결정을 `승인` 상태로 바꿨다.
- Fargate·ASG 단독·Compose·EKS 대안, 선택 이유, 운영 책임과 재검토 조건을 기록했다.
- Wiki의 초기 `t3.large`, ASG 2~6대, Task 2~10개를 Capacity 확정값이 아닌 검증 시작값으로 분리했다.
- Network→ECR/IAM→ALB/ECS→Develop CD→Rollback/부하 시험의 구현·검증 순서를 명시했다.

### 검증한 항목

- ADR의 문제 정의와 수치는 V1 최종 부하테스트의 30·60 RPS 통과와 약 189 RPS 최초 실패 증거를
  사용하며, 600 RPS를 보장값으로 표현하지 않는다.
- ECS Capacity Provider, Load Balancing, Circuit Breaker, Placement와 Managed Instance Draining의
  AWS 공식 문서를 결정 근거로 연결했다.
- 실제 Terraform/AWS Resource는 만들거나 변경하지 않았다.

### 남은 문제

- VPC·NAT/VPC Endpoint, RTO/RPO, P/G/L 배치, Redis·AI PostgreSQL 범위와 비용은 별도 ADR이 필요하다.
- Cloud `ARCHITECTURE.md`는 아직 V1 기준 문서이므로 V2 구현 전에 환경별 목표 Architecture 문서가
  필요하다.

### 다음 작업

1. V2 Develop의 Resource 이름·Tag·State·Account/Region 경계를 정의한다.
2. 기존 AWS V1 Resource를 읽기 전용으로 Inventory한다.
3. Terraform Bootstrap과 V2 Develop Root Module 구조를 작성하고 빈 Plan까지 검증한다.

## 2026-10-06 V2 전체 작업 우선순위·현재 상태 Audit

### 완료한 작업

- 4단계 Wiki 설계, Cloud 필수 문서, V1 부하테스트 결과, FE·BE Dockerfile과 CI, Cloud CD·Compose,
  Alerting Terraform과 V2 ADR을 대조했다.
- 사용자가 제시한 ECS, ECR, RDS, CI/CD, P/G/L, Backup·Rollback, Docker 최적화, 10,000명 Fixture와
  V1/V2 비교 시험에 Network·IAM·Terraform State·ALB·S3/CloudFront·Secret·RTO/RPO·장애 Drill·
  비용·보안 Gate를 추가했다.
- 의존성 기준 P0~P8 구현 순서, 완료 조건과 다음 작업을 `docs/V2_IMPLEMENTATION_PLAN.md`에 정리했다.

### 검증한 항목

- V1 기준선은 혼합 30 RPS 지속과 60 RPS Spike 성공, 약 189 RPS부터 Timeout 발생이며 V2 비교용
  k6 Script와 결과 Artifact가 존재한다.
- 현재 V2 ECS on EC2, ASG, ALB, ECR, RDS, S3/CloudFront와 P/G/L IaC는 아직 없다.
- FE·BE CI는 `develop` PR을 검증하지만 Image Publish는 `main` Push에서 Docker Hub로만 수행한다.
- V1 Production은 OIDC+SSM 수동 승인 CD와 Image 자동 Rollback이 있으나 ECS Circuit Breaker와
  DB Schema/Data Rollback은 없다.
- Backend Dockerfile은 Multi-stage이지만 CI의 사전 `bootJar` 뒤 Docker Build 내부에서 다시
  `bootJar`를 수행하므로 실행시간 최적화 여지가 있다.

### 남은 문제

- ECS on EC2 문서 불일치는 이후 `V2 Compute ADR 정합화` 작업에서 해소했다.
- Wiki는 CloudWatch 우선을 말하고 저장소의 V2 회고와 사용자 최신 요청은 P/G/L을 요구한다. 이번
  계획은 CloudWatch AWS Metric·Alarm을 유지하고 P/G/L을 통합 분석 계층으로 추가하는 안을 둔다.
- Redis·AI PostgreSQL/pgvector가 이번 V2 실제 구축 범위인지, P/G/L을 어디에 배치할지, RTO/RPO와
  SLO·월 예산의 승인값은 아직 열려 있다.
- V1 Alerting은 SNS 수동 시험까지 성공했지만 Alarm Action 활성화, 실제 Discord 화면 수신 확인과
  Terraform Remote State 이전이 남아 있다.

### 다음 작업

1. ADR-001과 4단계 Wiki 결론을 정합화하고 열린 결정을 ADR로 분리한다.
2. V2 Develop 우선의 Account·Region·이름·Tag·Network·State 경계를 설계한다.
3. 기존 AWS V1 Resource를 읽기 전용 Inventory한 뒤 Terraform Bootstrap과 Environment 구조를 만든다.
4. 실제 AWS 생성은 Plan, 예상 비용과 V1 무변경을 제시하고 사용자 승인 후 수행한다.

## 2026-10-06 V1 AWS 장애 알림 Local State 우선 적용 결정

- 복구 Plan `alerting-reconcile.tfplan` 적용이 성공해 Lambda 호출 권한과 SNS Subscription 2개를
  `2 added / 0 changed / 0 destroyed`로 생성했다. Terraform State에는 Data Source 1개와 Managed
  Resource 11개가 모두 기록됐다.
- SNS Topic에서 수동 INFO 메시지를 발행했고 Message ID
  `d8619d7d-c673-5350-9464-fe43e5dd09d7`을 반환받았다. Lambda가 1회 실행되어 오류 없이 종료됐고
  실행시간 6,247.54ms, 최대 메모리 92MB/128MB를 기록했다.
- Lambda가 Discord 오류 응답에서 예외를 발생시키도록 구현돼 있고 해당 Invocation에 오류 로그가
  없으므로 Webhook 요청은 성공한 것으로 판단된다. Discord `infra_error` 채널의 실제 메시지 도착은
  사용자 화면에서 최종 확인해야 한다.
- 최초 저장 Plan 적용 시 `Saved plan is stale`이 발생했다. Plan 생성 후 다른 Terraform 작업이
  Local State Serial을 10까지 변경했고, 실제 AWS와 State에는 9개 Managed Resource가 부분 생성된
  상태였다. 오래된 Plan은 적용하지 않았다.
- 실제 AWS를 읽기 전용으로 대조해 Lambda가 `Active`, SNS Topic과 Alarm 3개가 존재하고 Alarm의
  `AlarmActions`·`OKActions`가 비어 있음을 확인했다. 자동 Discord 경보는 아직 활성화되지 않았다.
- 부분 생성된 Lambda는 계정의 서울 Region 동시 실행 한도가 `10`인 상태에서 예약 동시 실행 `2`를
  설정하지 못해 taint된 것으로 확인했다. Lambda의 Runtime, Handler, Memory, Timeout, IAM Role,
  Environment Key와 VPC 미연결 상태는 코드와 일치했다.
- 현재 계정 한도에 맞춰 Lambda의 `reserved_concurrent_executions` 설정을 제거하고 정상 함수의 State
  taint를 해제했다. 재검증 Plan `alerting-reconcile.tfplan`은 Lambda를 교체하지 않고 SNS의 Lambda
  호출 권한과 Lambda Subscription만 `2 create / 0 update / 0 delete`로 추가한다.
- Production `sts get-caller-identity`에서 계정 `721744297924`, IAM 사용자 `peter.jung-infra`를
  재확인했다. 대상 EC2 `i-0417358a95b551c12`는 `seonjalal-v1-app`, `running` 상태다.
- Secrets Manager의 `seonjalal/production/discord-infra-alert` Secret 메타데이터를 읽기 전용으로
  확인했으며 Webhook 값 자체는 조회하거나 출력하지 않았다.
- 첫 적용 전 저장 Plan `alerting.tfplan`은 `11 create / 0 update / 0 delete`였으며 기존 EC2와
  애플리케이션 리소스를 수정하거나 삭제하지 않는 것을 확인했다.
- 첫 적용 전 `alarm_actions_enabled=false`와 세 CloudWatch Alarm의 빈 `alarm_actions`·`ok_actions`를
  Plan JSON으로 재검증했다. 현재도 Alarm Action ARN은 비어 있어 자동 Discord 경보는 활성화되지
  않았다.
- Discord 알림 경로를 먼저 검증하기 위해 첫 적용은 Terraform 기본 Local Backend를 사용하고,
  팀 공동 작업이나 CI 적용 전에 S3 Backend로 State를 이전하기로 결정했다.
- `operations/alerting/terraform/versions.tf`에서 선행 S3 Backend 요구를 제거했다. 첫 State는 같은
  Directory의 `terraform.tfstate`에 저장되며 `.gitignore`로 Git Commit이 차단된다.
- `operations/alerting/README.md`를 Local State 우선 절차와 이후 `init -migrate-state` 이전 절차에
  맞게 수정했다.
- `terraform fmt -check -recursive`, `terraform init -reconfigure`, `terraform validate`를 실행했고
  Local Backend 초기화와 구성 검증이 통과했다. 고정 Provider는 AWS `6.67.0`, Archive `2.8.1`이다.
- 첫 Plan 검증 시점에는 Apply 전이었고, 이후 부분 적용 복구와 `alerting-reconcile.tfplan` 적용까지
  완료해 현재 Managed Resource 11개가 Local State에 기록돼 있다.
- Local State 기간에는 현재 Mac 한 곳에서만 Terraform을 실행하고 `terraform.tfstate`를 삭제·이동·
  공유하지 않는다. 다음 단계는 실제 Discord 수신을 확인한 뒤 Alarm Action 활성화 Plan을 검토하고,
  V2 Terraform 작업 전에 공용 Remote State로 이전하는 것이다.

## 2026-10-05 V1 AWS 장애 알림 로컬 실행환경·Production 인증 준비 완료

- macOS Software Update로 Command Line Tools for Xcode 27.0을 설치했고, Package Version
  `27.0.0.0.1788430756`, Apple clang `21.0.0`과 활성 경로
  `/Library/Developer/CommandLineTools`를 확인했다.
- Homebrew로 Terraform `1.16.4` (`darwin_arm64`)를 설치했다. 프로젝트의 요구 조건
  `>= 1.7.0`을 충족한다.
- `operations/alerting/terraform`에서 `terraform fmt -check -recursive`,
  `terraform init -backend=false`, `terraform validate`가 모두 통과했다.
- 초기화 과정에서 AWS Provider `6.67.0`, Archive Provider `2.8.1`을 선택한
  `.terraform.lock.hcl`이 생성됐다. Provider Lock File은 Version Control 대상이고,
  `.terraform/`과 실제 State·입력 파일은 Git 제외 대상이다.
- Mac의 기존 `default` AWS Profile은 개인 계정 `155359186350`의 장기 Access Key를 사용하고 있어
  Production 작업에 사용하지 않는다.
- Production 계정 `721744297924`에 일상 작업용 IAM 사용자 `peter.jung-infra`와 별도 권한 그룹을
  준비하고, Root 대신 Console MFA와 `aws login` 임시 자격 증명을 사용하는 구조로 전환했다.
- AWS CLI Named Profile `seonjalal-production`의 `aws login`과 `sts get-caller-identity`가 성공했고,
  호출 주체 `arn:aws:iam::721744297924:user/peter.jung-infra`, Region `ap-northeast-2`를 확인했다.
- SNS, Lambda, CloudWatch Alarm, IAM Runtime Role과 Terraform State S3 Bucket은 아직 생성하지 않았다.
  따라서 Production 서비스와 Discord 알림 경로에는 실제 변경이 없다.
- 다음 시작점은 Production 계정에 공용 Terraform State S3 Bucket을 Bootstrap하고 `backend.hcl`로
  연결하는 작업이다. 이후 Plan을 검토하고 명시적 승인 후 Action 비활성 상태로 첫 Apply,
  SNS 수동 전송 시험, Alarm Action 활성화 순으로 진행한다.

## 2026-10-05 V1 AWS 장애 알림 1단계 코드 작성

- Discord 서버의 기존 `be_error`, `fe_error` 명명 규칙에 맞춰 인프라 알림 채널명을 `infra_error`로 확정하고 구축 가이드에 반영했다.
- 사용자가 `infra_error` 채널·Webhook과 Secrets Manager Secret 생성을 완료했다고 보고했으나 Secret 값은 공유하거나 조회하지 않았다.
- 사용자가 제공한 Secret ARN으로 Production 계정 `721744297924`, 서울 리전과 Secret 이름을 확인했다.
- Production 계정에는 상품 이미지용 S3 버킷 외 Terraform State 전용 버킷이 없다고 사용자 확인 결과를 기록했다. 상품 데이터와 State의 접근권한·수명주기가 달라 기존 버킷은 재사용하지 않는다.
- 기존 Production GitHub OIDC Role은 애플리케이션 배포 전용으로 유지하고, Terraform State 접근과 인프라 변경에는 별도 OIDC Role을 Bootstrap하는 방향으로 정했다. 실제 Role과 버킷은 아직 생성하지 않았다.
- 첨부된 CloudShell 결과에서 Production EC2 `seonjalal-v1-app`의 실행 상태, GitHub OIDC Provider와 immutable Repository ID·`main` Branch로 제한된 기존 Role Trust를 확인했다.
- 같은 결과에서 CloudShell 호출 주체가 `arn:aws:iam::721744297924:root`임을 확인했다. Root는 일상 Terraform·CloudShell 작업에 사용하지 않고 MFA를 보호한 뒤 IAM Identity Center 또는 관리자 역할로 전환해야 한다.
- Terraform State는 Discord 알림 기록이 아니라 Terraform 리소스 주소와 실제 AWS Resource ID·의존성 정보를 연결하는 필수 관리 데이터임을 가이드에 명시했다.
- Production CloudShell에서 확인한 계정, 리전, EC2 Instance ID와 Secret ARN을 Git 미추적 `operations/alerting/terraform/terraform.tfvars`에 준비했다. Webhook 값은 포함하지 않았고 Alarm Action은 비활성 상태다.
- 당시 Mac의 Command Line Tools와 Terraform 설치, Production AWS 인증이 준비되지 않아 적용을
  중단했으며, 이후 위의 `로컬 실행환경·Production 인증 준비 완료` 작업에서 해결했다.
- V1 EC2의 System Status, Instance Status와 지속 CPU 포화를 감시하는 Terraform 구성을 `operations/alerting/terraform/`에 추가했다.
- CloudWatch Alarm의 ALARM·OK 이벤트를 SNS로 받아 Discord Embed로 변환하는 Python Lambda를 추가했다.
- Discord Webhook URL은 Terraform State나 Lambda 환경변수에 저장하지 않고 지정된 Secrets Manager Secret 하나에서만 조회하도록 IAM 권한을 제한했다.
- 첫 Terraform 적용에서는 `alarm_actions_enabled=false`를 기본값으로 두고, SNS 수동 시험 후 별도 Plan으로 Action을 활성화하도록 구성했다.
- Lambda는 Discord 송신을 위해 VPC 밖에서 실행하며, 메시지의 Discord mention을 비활성화하고 Webhook과 원본 SNS 메시지를 로그로 출력하지 않는다.
- Lambda 메시지 변환·SNS 전달 단위 테스트 2개가 Python 3.14 로컬 환경에서 통과했다.
- Terraform 형식·초기화·정적 검증은 이후 완료했으며, 원격 State Backend가 없어 Plan은 아직
  수행하지 않았다.
- 실제 Discord 채널·Webhook, Secrets Manager Secret, SNS, Lambda와 CloudWatch Alarm은 생성하지 않았다. 대상 계정·환경·원격 State Backend와 예상 비용 확인 후 승인받아 적용해야 한다.
- 다음 단계는 원격 State Bootstrap, Plan 검토, Action 비활성 상태의 첫 적용과 SNS 수동 시험이다.

## 2026-10-05 문서 근거자료 규칙 추가

- `AGENTS.md`에 인프라 설계, 기술 선택, 운영 절차, 수치 제안의 근거자료 링크 첨부 원칙을 추가했다.
- 공식 문서를 우선하고 해외 기술 블로그와 논문은 보조 근거로 사용하며, 검색 결과나 홈페이지가 아닌 실제 원문 링크를 사용하도록 명시했다.
- 저장소 내부 사실은 파일 경로 또는 코드 위치를 함께 제시하고, 외부 사실·가정·제안을 구분하도록 명시했다.
- AI 저장소를 읽기 전용으로 확인한 결과 현재 `main` 브랜치만 있고 GitHub Actions 워크플로는 없다.
- AI 저장소의 `profiling`과 `product-search`는 런타임, 테스트, 데이터 의존성이 달라 독립 CI 이미지와 ECS 서비스로 분리하는 설계가 필요하다.
- 다음 작업은 V2 네트워크, 배포, 데이터베이스, 관측성 설계를 ADR과 Terraform 모듈 단위로 확정하고, AI 팀과 `develop` 브랜치 및 필수 테스트 범위를 합의한 뒤 CI를 구현하는 것이다.

## 2026-10-05 GitHub Wiki 사용자 수정사항 로컬 동기화

- GitHub Wiki 원격 Commit `672b8ff`을 기준으로 로컬 `wiki/` 원고와 비교했다.
- 원격에서 변경된 `Home.md`와 `V1-SLI-SLO.md`만 로컬에 반영했다.
- `Home.md`에는 사용자가 등록한 선잘알 로고 이미지가 추가됐다.
- 나머지 13개 Wiki 문서는 원격과 로컬이 동일했고 추가·삭제된 페이지는 없었다.
- 동기화 후 15개 Wiki 파일이 원격 내용과 동일한지 Byte 단위로 검증한다.

## 2026-10-05 V1 SLI/SLO 벤치마크 근거 보강

- `wiki/V1-SLI-SLO.md`를 V1.1로 개정해 외부 벤치마크의 적용 범위와 한계를 문서화했다.
- AWS Single EC2 99.5%는 V1 가용성 후보의 참고값, AWS Well-Architected의 온라인 커머스
  99.95% 예시는 V2·V3 장기 목표 비교값으로 분리했다.
- Google Core Web Vitals의 p75 LCP 2.5초, INP 200ms, CLS 0.1을 Frontend 사용자 경험 SLO
  후보로 추가했다. 실제 RUM 계측은 아직 구현되지 않았다.
- 접근 가능 인원 250명을 MAU로 단정하지 않고, DAU 35명·630 requests/day에서 계산한 사용자당
  약 18 requests/day를 초기 Traffic Model 입력으로만 기록했다.
- 30 RPS 지속과 60 RPS Spike는 운영 SLO가 아니라 V1 Release Capacity이며, 선물 생성 TPS는
  쓰기 무결성 시험 전까지 미정으로 유지했다.
- 다음 작업은 Product·Backend·Frontend와 목표 승인, MAU·Peak 집중 비율·선물 생성 전환율의
  계측 책임 확정, Nginx SLI 집계와 Frontend RUM 구현이다.

## 2026-10-04 Cloud Wiki 원고와 V1 SLI/SLO 승인 후보 작성

- GitHub Wiki를 주차가 아닌 V1·V2·V3 버전 중심으로 탐색하고, 날짜·주차 기록은 버전별 작업
  일지에서 관리하는 정보 구조를 정했다.
- `wiki/Home.md`, `_Sidebar.md`, `_Footer.md`와 서비스·V1·V2·V3·용어·게시 가이드 원고를
  작성하고 GitHub Wiki Commit `3e0faa8`로 게시했다.
- V1의 기준 SLI/SLO 원고를 `wiki/V1-SLI-SLO.md`로 정리했다. rolling 30일 Core API 가용성
  99.5%, 성공한 읽기 요청의 95%는 500ms 이내·99%는 1초 이내, 외부 Web 확인 99.5%를 승인
  후보로 두었다.
- AWS SLA, 운영 SLO, k6 Release Gate를 분리하고 30 RPS·60 RPS 시험 통과가 월간 SLO 달성을
  증명하지 않는다는 한계를 명시했다.
- 기존 `load-tests/SLI-SLO.md`는 V1에 없는 보낸 선물 조회와 과거 수치를 포함해 기준 문서에서
  제외하고 새 Wiki 원고를 안내하도록 변경했다.
- 다음 작업은 Product·Backend·Cloud 담당의 SLO 목표·분모·제외 규칙 승인, GitHub Wiki 최초
  페이지 생성과 게시, Nginx 사용자 경계 Log·Synthetic Check·Dashboard 구현이다.

## 2026-10-02 V1 읽기·혼합 부하테스트 완료

- V1 핵심 9개 API의 Smoke와 5 RPS·5분 API별 Baseline을 완료했다. 모든 유효 실행에서 HTTP·Business
  Error와 Dropped Iteration은 0이고 API별 p95는 87.40~118.42ms였다.
- 혼합 Baseline 5 RPS, Capacity의 30 RPS 20분 구간과 5→60→5 RPS Spike가 성공률 100%,
  Dropped Iteration 0으로 통과했다.
- Stress는 약 189 RPS에서 최초 5초 Timeout이 발생했고 약 204 RPS에서 누적 실패율 5.12%로 안전
  중단됐다. 평균 RPS 75.63은 Ramp 전체 평균이며 한계 처리량으로 사용하지 않는다.
- Stress에서 Host CPU 100%, Backend CPU 120.56%, MySQL CPU 75.32%, 실행 대기 작업 22개가
  관측됐다. Host Nginx 499 3,727건, 502 8건과 `worker_connections 1024` 경고가 발생했다.
- MySQL 200ms 이상 Slow Query, OOM과 Container Restart는 0이었고 시험 종료 후 세 Container가
  Healthy, Health API HTTP 200·17.74ms로 자동 회복했다.
- V1 병목은 단일 EC2의 공유 CPU와 Backend 처리 대기이며, Nginx Connection 한계가 연쇄적으로
  나타난 것으로 판정했다. Nginx 한도만 높이는 것을 근본 해결로 보지 않는다.
- HS256 JWT를 k6 시작 시 환경변수 Secret으로 직접 생성해 1시간 Token 재발급 작업을 제거했다.
  Secret은 Source·Git·결과물에 기록하지 않는다.
- Host Nginx에 Develop 허용 Network 화이트리스트를 추가했다. 공유 Wi-Fi 사용자는 개인별로
  구분하지 못하므로 Identity 기반 접근 또는 VPN은 V2 개선 항목이다.
- 실제 Gift Create와 로그인·회원가입 부하는 재고·Idempotency·Rate Limit·DB Reset이 필요한 별도
  쓰기 무결성 시험으로 남겼다.
- 전체 결과와 쉬운 용어 설명은 `docs/2026-10-02-v1-load-test-final-report.md`에 정리했다.

## 2026-10-02 PL 미팅 검토와 우선순위 재정렬

- PL이 Develop에 실제 접속하고 회원가입까지 했으므로 `관리자 공인 IPv4 /32에서만 접근 가능`하다는
  기존 가정은 더 이상 유효하지 않다. Security Group Inbound CIDR, 공유 NAT 여부와 Host Nginx
  Access Log의 Source IP를 읽기 전용으로 대조하는 것을 최우선 작업으로 정했다.
- Develop 접근 허용과 부하 발생기의 Rate Limit 예외는 별도 통제로 관리한다. 의도하지 않은 접근의
  임시 차단은 V2로 미루지 않고, VPN·Identity Access의 완성형 구성은 비교 후 V2에서 진행할 수 있다.
- 현재 Fixture는 사용자 500명, 친구 관계 5,689건, 완료된 받은 선물 4,864건이며 Persona별 분포와
  목적을 문서화했다. 데이터는 시험 목적 없이 무작정 늘리지 않고 Pagination, Index, Cache 또는
  V2 규모 등 검증 조건을 먼저 정한 뒤 증설한다.
- SLI와 잠정 SLO를 먼저 정의하고 V1·V2·V3 실측으로 보정하는 기존 방침을 유지한다. AWS SLA는
  목표의 입력일 뿐 선잘알의 사용자 요청 기반 SLI가 아니다.
- k6에서 JWT를 직접 만들 수는 있지만 HS256 서명 Secret을 Mac이나 Source에 전달하는 방식은
  채택하지 않는다. 현재 로그인 기반 Token Pool을 개선하고, 필요하면 Develop EC2 경계 안에서만
  제한적으로 발급하는 방식을 Fullstack과 검토한다.
- Application APM 계측과 MQ 사용 방식은 Fullstack 영역, 수집·저장·Dashboard와 MQ Runtime Infra는
  Cloud 영역으로 구분하고 관측·운영 계약은 공동 결정한다.
- 세부 판단, 역할 경계와 이번 주/V2 구분은
  `docs/2026-10-02-pl-meeting-review-and-action-plan.md`에 정리했다.

## 2026-10-01 오늘 종료 상태: Smoke 전체·상품 카테고리 Baseline 완료

- 오늘은 V1 핵심 API별 Smoke, 혼합 Smoke와 상품 카테고리 Baseline까지 완료하고 추가 부하 주입을
  중단했다.
- Slow Query Log와 Background Metric Collector는 정상 종료했으며 실행 중인 시험 Process는 없다.
- 나머지 API별 Baseline 8개와 혼합 Baseline은 다음 작업으로 남겼다.
- 다음 시작점은 시험 추가 실행이 아니라 `product-categories.js`와 공통 Profile·HTTP Helper를
  사용자가 직접 설명할 수 있도록 줄 단위로 검토하는 것이다.
- 상품 카테고리 Baseline의 실제 최대 VU는 5였고 현재 Token 배열의 앞 5개가 모두 Light 사용자라
  30개 Persona Pool 전체가 사용되지는 않았다. 상품 카테고리 결과에는 영향이 작지만 친구·선물
  Baseline 전에는 Token을 Persona 교차 순서로 배치하고 낮은 VU의 분포를 검증해야 한다.
- 오늘 전체 작업과 결과, 미검증 범위는
  `docs/2026-10-01-v1-load-test-smoke-baseline-worklog.md`에 정리했다.

## 2026-10-01 V1 기능 Smoke 완료

- V1 핵심 9개 Endpoint의 API별 Smoke와 혼합 Smoke를 모두 실행했다.
- 혼합 Smoke는 1 VU가 9개 Endpoint를 각각 한 번 호출했으며 Status·응답 계약 18/18,
  HTTP 실패와 Business Error 0, `k6_exit=0`으로 통과했다.
- `20261001T121321Z-mixed-read-smoke`의 응답시간은 평균 52.33ms, p95 117.16ms,
  최대 132.93ms였으나 9개 표본이므로 성능 SLO 증거로 사용하지 않는다.
- 12:06 UTC 이후 API·혼합 Smoke 21건이 Host Nginx와 Frontend Nginx에서 모두 HTTP 200으로
  일치했고 Backend `RequestLoggingFilter`에도 같은 21건이 HTTP 200으로 기록됐다.
- 상품 상세 조회 후 Test1·상품 896의 `product_views`는 1건, 상품 조회 수는 25,692로 한 번만
  증가했으며 재고는 100으로 유지되어 24시간 중복 조회 방지와 사전 검증의 무부작용을 확인했다.
- 0.2초 안에 끝나는 Smoke에서 k6 HTML 생성을 건너뛰는 사례가 있어 Smoke Dashboard 수집 주기를
  100ms에서 10ms로 조정했고, 재실행에서 163KB HTML Report 생성을 확인했다.
- 만료된 1시간 Access Token으로 상품 상세 Smoke가 HTTP 401 3건·`k6_exit=99`를 반환한 실행은
  성능 실패와 구분되는 인증 실패 증거로 보존했다. Token 갱신 후 같은 Smoke는 정상 통과했다.
- 다음 단계는 Baseline 시작 전 Light·Normal·Heavy 대표 사용자의 Token Pool을 준비한 뒤, 각
  Test ID마다 Container·Host 지표와 Nginx·Backend·MySQL Slow Query를 함께 수집하는 것이다.
- Token 준비 Script의 Bash 비밀번호 Prompt와 Signal 종료 처리를 수정하고 빈 입력·임시 파일 정리를
  검증했다. Light·Normal·Heavy 각 10명, 총 30명의 Token 발급이 로그인 IP 제한의 429 재시도를
  포함해 성공했으며 JSON 계약, 개수 30개와 파일 권한 `600`을 확인했다.
- 다음 실행은 `product-categories-baseline`이며 k6를 시작하기 전에 EC2 수집 Script 존재 여부와
  Test ID별 증거 Directory 구성을 확인한다.
- 상품 카테고리 Baseline `20261001T124646Z-product-categories-baseline`은 5 RPS·5분 동안 정확히
  1,500건을 처리했다. Status·계약 성공률 100%, HTTP·Business Error 0, Dropped Iteration 0,
  평균 36.26ms, p95 99.28ms, p99 113.71ms로 잠정 Gate를 통과했다.
- Host Nginx·Frontend Nginx·Backend의 성공 요청이 각각 1,500건으로 일치하고 세 계층의 4xx·5xx와
  Host Nginx Error는 0건이었다. MySQL 200ms 이상 Slow Query도 0건이다.
- 카테고리 Query 1,500회의 평균 DB 시간은 0.380ms, 인증 사용자 Query는 0.272ms였다. Backend
  평균·최대 CPU는 8.19%·8.44%, Host 평균·최대 CPU는 6.09%·25%로 지속적인 자원 포화가 없었다.
- k6 최대 735.22ms와 대응하는 Backend 최대 691ms Outlier 7건이 시험 시작 5초 뒤 같은 시각에
  모였지만 DB 지연·오류·동시각 CPU Steal은 없었다. 첫 실행 JVM/JIT·GC·Scheduling의 일시 지연
  가설로 기록하고 다음 API Baseline의 시작 구간에서 재현 여부를 비교한다.
- MySQL `SET GLOBAL long_query_time`은 기존 Connection에 적용되지 않으므로 Develop Backend를
  시험 전에 한 번 재시작했고, 가이드의 확인 명령을 `SHOW GLOBAL VARIABLES`로 수정했다.

## 2026-10-01 실행 안내 규칙 및 로그 경로 검증

- 사용자가 직접 실행할 코드와 명령에는 목적, 실행 위치, 영향과 주의사항을 한국어 주석으로
  설명하도록 `AGENTS.md`에 규칙을 추가했다.
- Develop 환경 계약 검증이 통과했고 FE `1.3.2`, BE `1.3.0`, MySQL `8.4` 컨테이너가 모두
  Healthy인 것을 사용자 실행 결과로 확인했다.
- Frontend Nginx Access/Error Log가 stdout/stderr로 연결되어 `docker logs`로 수집 가능하고,
  Host Nginx의 Access/Error Log는 `/var/log/nginx/`에서 파일로 수집 가능한 것을 확인했다.
- 실제 적용 설정에서 Host Nginx가 `dev.seonjalal.com:443`을 받아 `127.0.0.1:8081`로 전달하고,
  Frontend Nginx가 정적 파일을 서빙하며 `/api/`를 `backend:8080`으로 전달하는 것을 확인했다.
- Mac의 고유 User-Agent Probe가 동일 시각에 Host Nginx, Frontend Nginx와 Backend에 모두 남고
  HTTP 200을 반환해 전체 요청·로그 경로를 검증했다.
- 현재 DB는 사용자 12명, 친구 관계 29건, 선물·상품 조회 기록 0건, 상품 4,231건이며 Flyway
  V1~V12가 모두 성공 상태인 것을 확인했다.
- Fixture 생성 전 `A-pre-fixture-20261001T064129Z.sql.gz` Backup을 생성하고 gzip 무결성과
  SHA-256 `7246ea62f1c60d99ca1a5bb99a67e2c0a67849fee6a1a48e33d97b6cbfeee0ce`를 확인했다.
- Fixture 적용 후 `B-fixture-ready-20261001T073008Z.sql.gz` Backup을 생성하고 gzip 무결성,
  `600` 권한과 SHA-256
  `196840692f25c4cc6357367781464af0d1c0ea3eba615f8f4a8d52b9928c7ba0`을 확인했다.
- Test1 로그인 사전 확인이 HTTP 200과 응답 계약 검증을 통과했다. 이는 k6 Smoke가 아니라
  인증 Fixture·Token 준비 단계다. Access Token TTL은 3,600초이고
  Test1의 실제 User ID는 524이며 Token은 Git Ignore된 `load-tests/secrets/`에 `600` 권한으로 둔다.
- 실제 k6 사용자 프로필 Smoke `20261001T080017Z-user-profile-smoke`가 1 VU·3 Iteration으로
  실행되어 Status·응답 계약 6/6, HTTP 실패와 Business Error 0, `k6_exit=0`으로 통과했다.
- 위 Smoke의 응답시간 p95 163.65ms는 표본이 3개뿐이므로 성능 SLO 증거로 사용하지 않는다.
- API별 k6 Smoke에서 상품 카테고리, 상품 목록, 친구 목록, 받은 선물 목록이 각각 1 VU·3회,
  Status·계약 성공률 100%, HTTP 실패와 Business Error 0, `k6_exit=0`으로 통과했다.
- Frontend와 Backend 로그에서 위 요청 15건의 HTTP 200과 경로를 확인했다. 상품 목록은 약 41KB
  응답에서 k6 최대 479.23ms, Backend 처리 58~69ms였으나 표본 3개이므로 Baseline에서 재측정한다.
- Test1~Test500과 Light/Normal/Heavy 친구·받은 선물 분포를 한 Transaction으로 만드는
  `load-tests/fixtures/v1-load-test-fixture.sql`을 작성했다. gzip·Base64 방식으로 Develop EC2에
  전송하고 MySQL 임시 테이블 다중 참조 제약을 반영해 수정했다.
- 최종 Fixture 적용과 독립 검증이 성공했다. Test 사용자 500명, 친구 관계 5,689건, 완료된 받은
  선물 4,864건이며 모든 Test 사용자가 받은 선물을 가지고 자기 자신 친구·선물 관계는 0건이다.
- 앞선 실패 Transaction은 모두 Rollback됐으나 MySQL Auto Increment는 되돌아가지 않아 최종 Test
  User ID 범위는 524~1,023이다. k6는 이 범위를 하드코딩하지 않는다.
- 상품 상세 조회가 `product_views`를 변경하므로 Fixture 적용 후 `B-fixture-ready` Backup을 만들어
  반복 시험의 동일 시작 상태로 사용해야 한다.

## 2026-10-01 V1 잠정 SLI·SLO와 부하시험 실행 준비

### 완료한 작업

- 사용자 Journey를 기준으로 V1 잠정 SLI·SLO와 부하시험 Release Gate를 정의했다.
- AWS Single EC2 SLA를 입력으로 V1 월간 가용성 99.5%, V2 후보 99.9%를 첫 검증 가설로 두었다.
- 읽기 p95 500ms·p99 1초는 기존 k6 비교 Gate로 유지하되 확정 사용자 SLO와 분리했다.
- API별 Profile에 `spike`를 추가하고 `stress`를 기존 `breakpoint` Alias로 지원했다.
- 보낸 선물 조회를 제외하고 V1 핵심 9개 Endpoint를 가설 비율로 호출하는
  `workloads/mixed-read.js`를 추가했다.
- Test ID별 k6 HTML·JSON·Summary와 시험 중 Container·Host 지표 수집 절차를 작성했다.
- `collect-load-test-metrics.sh`의 Shell 문법과 k6 2.3.0의 Smoke·Spike·Stress 실행 요구사항을 검증했다.
- Develop `/`, `/api/health`, 상품 목록의 외부 200과 인증 API의 무인증 401을 확인했다.
- 잘못된 Bearer Token을 사용한 상품 목록 k6 Smoke가 3/3 모두 401로 실패하는 것을 증거로 남겼다.
- `K6_WEB_DASHBOARD_PERIOD=100ms`에서 짧은 Smoke도 HTML Report가 생성됨을 확인했다.

### 현재 Blocker

- Develop 유효 Access Token과 혼합 시험용 상품·사용자·선물 Fixture ID가 현재 Shell에 없다.
- 사용자가 EC2에 직접 접속할 수 있으므로 서버 측 수집 명령은 단계별 수동 실행한다.

### 다음 작업

1. 실제 적용 중인 Host·Frontend Nginx 설정을 읽기 전용으로 확인한다.
2. 부하시험 전 DB Backup과 Develop Test Account Pool·고정 Fixture ID를 준비한다.
3. API별 Smoke → 혼합 Smoke → API별 Baseline → 혼합 Baseline을 실행한다.
4. 앞 단계 통과 후 Capacity → Spike → Stress/Breakpoint를 실행한다.
5. 결과로 잠정 SLO, 혼합 API 비율과 V2 관측 요구사항을 보정한다.

## 2026-10-01 V1 종료 범위와 V2 전환 계획 변경

### 결정

- V2 개발 시작까지 4일만 남았으므로 V1 Develop 자동 CI/CD 구현은 보류한다.
- V1 Develop에는 FE `1.3.2`, BE `1.3.0` Release Image를 수동으로 고정 배포한다.
- V1의 남은 목표는 기능 Smoke, 로그 수집, 부하테스트 실행과 결과 보존이다.
- Prometheus·Grafana·Loki의 완성형 구성과 Develop 자동 배포는 V2 구조에서 구현한다.
- V1에서는 CloudWatch와 Docker·Nginx·MySQL 진단 자료로 최소 증거를 확보한다.

### Production 보호 경계

- Cloud `main`, Production Workflow, Production EC2와 Production DB는 변경하지 않는다.
- Develop 작업은 `/opt/seonjalal-develop`, `.env.develop`, `compose.develop.yaml`만 사용한다.
- 이미지 Tag를 `latest`로 바꾸지 않고 FE `1.3.2`, BE `1.3.0`을 명시한다.
- Backend 교체 전 Develop MySQL을 논리 Backup한다.

### 완료한 작업

- 배포 전 실행 Image ID와 Flyway V1~V11 적용 상태를 기록했다.
- Develop MySQL을 `mysqldump --single-transaction`으로 Backup하고 gzip 무결성을 검사했다.
- FE `1.3.2`, BE `1.3.0`을 Pull하고 Database를 재생성하지 않은 채 순차 교체했다.
- Backend 기동 중 Flyway V12 `seed signup terms`가 성공적으로 적용됐다.
- FE·BE·MySQL 세 Container가 모두 Running·Healthy 상태임을 확인했다.
- `verify.sh develop`의 Compose, Frontend와 Backend Routing 검증이 모두 통과했다.
- `dev.seonjalal.com` 외부 접근을 확인했다.

### 오늘 종료 상태와 다음 시작점

- 오늘 작업은 V1 Develop 최신 Image 수동 배포 완료 지점에서 종료한다.
- 다음 작업은 기능 Smoke와 부하테스트 실행 전 Log 수집 경로 검증부터 시작한다.
- `docs/guides/v1-final-load-test-and-log-collection.md`의 4단계부터 재개한다.
- 이어서 Run ID 생성, MySQL Slow Query Log 활성화, Smoke → Baseline → API별 Read/Write →
  User Journey 순으로 시험한다.
- 각 실행의 k6 Summary, Image ID, Container·Host Log, MySQL Slow Query와 CloudWatch Metric을
  같은 Test ID로 보존한다.

### 문서

- `docs/guides/v1-final-load-test-and-log-collection.md`
- `docs/wiki/v1-retrospective-and-v2-requirements.md`
- `docs/2026-10-01-v1-develop-manual-release-worklog.md`

## 2026-09-30 Develop 환경 전환·기동 완료

### 완료한 작업

- V1의 비운영 환경을 별도 Staging이 아닌 공유 Develop 환경으로 재정의했다.
- Cloud PR #17을 `develop`에 병합하고 Develop EC2에서 `77d77a2`까지 fast-forward했다.
- 활성 구성 이름을 `.env.develop`, `compose.develop.yaml`, `seonjalal-develop`로 전환했다.
- `/opt/seonjalal-staging`을 `/opt/seonjalal-develop`으로 이동하고 새 Develop DB Volume을 생성했다.
- FE·BE `1.1.0`과 MySQL 8.4를 기동해 세 Container의 Health를 확인했다.
- `dev.seonjalal.com` DNS·수동 DNS 인증서·Nginx Reverse Proxy를 적용했다.
- EC2 내부와 허용된 개발자 네트워크에서 Frontend와 `/api/health` HTTPS 응답을 확인했다.
- 휴대전화 모바일망에서 접속이 차단되는 것을 확인해 Security Group의 개발자 IPv4 `/32`
  제한이 실제로 동작함을 검증했다.
- 기존 `seonjalal-staging-db-data`는 복구 대비용으로 삭제하지 않고 보존했다.

### 주요 트러블슈팅

- 원격 Feature Branch가 보이지만 추적 Branch로 전환되지 않은 문제는 Single-branch Clone의
  RefSpec과 배포 서버에서 Feature를 직접 실행하려 한 흐름이 원인이었다. PR을 `develop`에
  병합한 뒤 `git pull --ff-only origin develop`로 해결했다.
- `verify.sh`의 `service "frontend" is not running`은 Image Pull 이후 `docker compose up -d`를
  누락해서 발생했다. Stack을 기동한 뒤 재검증해 해결했다.
- Scheme을 생략한 `dev.seonjalal.com` 접속 실패는 Browser가 HTTP 80을 먼저 시도하지만 Security
  Group에서 80을 닫아둔 결과다. 명시적인 `https://dev.seonjalal.com` 443 접속은 정상이다.
- 수동 DNS 방식으로 발급한 Let's Encrypt 인증서는 Hook이 없어 자동 갱신되지 않는다.

### 현재 경계

- Cloud의 `develop`까지만 반영했으며 `main`에는 아직 반영하지 않았다.
- Production Compose, Production EC2와 기존 Production CD는 변경하지 않았다.
- Develop 자동 배포는 아직 없으며 현재는 EC2에서 수동으로 Pull·Compose 기동한 상태다.
- `dev.seonjalal.com`은 현재 관리자 공인 IPv4 `/32`에서만 접근 가능하다.
- 기존 Staging Route 53 Record·Nginx 설정·인증서·DB Volume의 정리는 Develop 최종 검증 뒤 수행한다.
- Portainer 구성 제거는 작업 트리에만 존재하며 별도 검토·커밋이 필요하다.

### 접근 제어 결정 필요

- 팀원에게 Security Group 변경 권한을 반복 부여하는 방식은 기본안으로 채택하지 않는다.
- IAM은 Application 로그인 수단이 아니며, Security Group 편집 권한은 실수로 `0.0.0.0/0`을
  허용하거나 다른 Instance 규칙을 변경할 위험이 있다.
- 단기에는 승인된 팀 네트워크 `/32` 추가, 중기에는 IAM Identity Center + SSM Port Forwarding,
  Tailscale 또는 Cloudflare Access처럼 사용자 Identity 기반 접근을 비교한다.

### 다음 작업 우선순위

1. FE·BE `develop` Candidate Image 생성과 Develop 자동 배포·Health·Rollback·Freeze를 설계한다.
2. Production Workflow와 Secret을 변경하지 않는 별도 Develop CD를 구현한다.
3. Prometheus·Grafana·Loki 도입 범위와 수집 대상을 정하고 자원 간섭을 검증한다.
4. 임시 SLI를 먼저 정의하고 관측 환경을 구성한 뒤 Fixture·부하 시험을 수행한다.
5. 측정 결과를 바탕으로 SLO와 Release Gate를 보정한다.

### 상세 기록

- `docs/2026-09-30-develop-environment-migration-and-troubleshooting.md`
- `docs/ARCHITECTURE.md`

## 2026-09-30 Production 환경변수·버전·Health Check 트러블슈팅

### 확인한 원인

- Production CD는 기준 `.env`를 수정하지 않고 `release.candidate.env`와
  `release.current.env`에 배포 버전을 기록한다.
- CD 이후 기준 `.env`로 Backend만 수동 재생성해 FE `1.3.0`, BE `1.0.0` 혼합 버전 상태가
  발생했다.
- `seonjalal.com`은 `www.seonjalal.com`으로 `301` Redirect하며, `curl -I`의 HEAD Health
  요청은 GET 전용 익명 허용 규칙에 포함되지 않아 `401`을 반환했다.

### 복구·검증

- Production CD를 FE·BE `1.3.0`으로 다시 실행했다.
- 실제 실행 이미지가 모두 `1.3.0`인 것을 확인했다.
- Discord·Google Sheets 환경변수 네 개가 Backend Container에 전달된 것을 값 노출 없이
  확인했다.
- EC2 내부 GET `/api/health`가 `status=UP`을 반환하는 것을 확인했다.
- 상세 절차와 재발 방지를
  `docs/troubleshooting/2026-09-30-production-release-env-version-healthcheck.md`에 기록했다.

### 남은 작업

1. EC2에서 수동 반영한 Backend 환경변수 전달 설정을 Cloud 저장소에도 반영한다.
2. Production Runbook에 `.env`와 릴리스 환경 파일의 역할을 명시한다.
3. 선언 버전과 실제 Container Image의 일치 여부를 배포 검증에 추가할지 검토한다.

## 2026-09-30 Staging에서 Develop 환경으로 전환 계획 (완료 현황은 문서 상단 참고)

이 절은 구현 전 계획 Snapshot이다. DNS·TLS·Nginx와 Compose 기동의 최신 완료 상태는
`2026-09-30 Develop 환경 전환·기동 완료` 절을 기준으로 한다.

### 결정

- V1에서는 별도 Staging 서버를 운영하지 않고 현재 비운영 EC2를 공유 Develop 환경으로 사용한다.
- Develop은 `develop` 브랜치의 Candidate Image를 자동 배포해 통합·기능·부하 시험을 수행한다.
- 릴리스 QA와 부하 시험 중에는 자동 배포를 동결하고 검증 대상 FE·BE Image Digest를 고정한다.
- Production은 Develop에서 검증한 동일 Image Digest만 수동 승인 후 배포한다.
- 과거 Staging 설계·작업 기록은 결정 당시의 이력으로 유지하고 활성 구성만 Develop 명칭으로 전환한다.

### 활성 환경 계약

- Domain: `dev.seonjalal.com`
- Compose Overlay: `compose.develop.yaml`
- Environment File: `.env.develop`
- Compose Project: `seonjalal-develop`
- DB·User: `seonjalal_develop`
- DB Volume: `seonjalal-develop-db-data`
- JWT Issuer: `seonjalal-develop`
- 배포 경로: `/opt/seonjalal-develop`
- 관리 접속은 SSM만 사용하고 443은 승인된 개발자·운영자 IPv4 `/32`만 허용한다.

### 다음 작업

1. AWS의 EC2 Name·Environment Tag를 Develop 기준으로 정리한다.
2. `dev.seonjalal.com` DNS·TLS·Nginx를 구성하고 기존 Staging Domain을 제거한다.
3. 신규 Develop DB Volume에서 Flyway·S3·기능 Smoke를 검증한다.
4. FE·BE `develop` Candidate Build와 Develop 자동 배포·Rollback·Freeze를 구현한다.
5. Fixture·최소 관측 환경을 준비한 뒤 API·Journey 부하 시험을 실행한다.

## 2026-09-29 Staging 우선순위·Fixture·DB 비용 전략 결정

### Staging 구성 착수

- `compose.staging.yaml`에 Staging 재시작 정책, SQL Log 비활성화와 전용 DB Volume 계약을 추가했다.
- `.env.staging.example`에 Candidate Image, 전용 DB·Secret·CORS·JWT 설정 예시를 추가했다.
- `scripts/validate-staging-env.sh`로 운영값 혼입과 Placeholder Secret을 배포 전에 차단한다.
- `scripts/verify.sh`가 Staging Compose Overlay를 검증할 수 있도록 확장했다.
- `staging.seonjalal.com` Host Nginx·TLS Reverse Proxy 예시와 README 실행 가이드를 추가했다.
- Staging Web은 공개하지 않고 443을 개발자·운영자 `/32` Allowlist로 제한한다. 관리 접속은
  SSM만 사용하며 22·8081·3306은 열지 않는다.
- Shell 문법, 유효·무효 환경변수 검증, Staging Compose Configuration과 `git diff --check`를 통과했다.

실제 AWS Resource, DNS와 인증서는 아직 생성·변경하지 않았다. 대상 AWS Account·예산·EC2
구성을 확인한 뒤 진행한다.

### 결정

- Grafana 기반 k6 결과 시각화는 Staging·Fixture·Reset과 첫 부하 결과가 준비된 뒤 진행한다.
- 현재 최우선 작업은 운영과 분리된 Staging 환경 구축이다.
- 더미 데이터 100~500개는 첫 기능·Journey 검증의 시작점으로만 사용하고, 성능 시험의 충분
  조건으로 간주하지 않는다.
- 현재 상품 Catalog는 이미 4,231개이므로 사용자·친구·선물·상품 조회 이력의 분포를 우선
  보강한다.
- H2는 MySQL/RDS 성능 시험에 사용하지 않는다. Backend 통합 테스트는 기존 Testcontainers
  MySQL을 유지하고, V1 Staging은 MySQL 8.4 Container를 사용한다.
- V2의 일상 성능 시험은 필요 시 생성·삭제하는 Single-AZ RDS를 우선 검토하고, Multi-AZ는
  HA·Failover와 최종 용량 검증 Window에만 짧게 사용한다.
- Aurora Serverless v2 Auto-pause는 비용 대안이지만 표준 RDS MySQL Multi-AZ와 동일한 성능
  환경으로 간주하지 않는다.

### 검증한 항목

- Backend는 H2가 아니라 Testcontainers MySQL을 테스트 의존성으로 사용한다.
- `V10__seed_product_catalog.sql` 생성기는 상품 4,231개를 전제로 한다.
- RDS DB Instance는 중지 중에도 Storage·Backup 비용이 발생하며 7일 후 자동 시작한다.
- 지원 Version의 Aurora Serverless v2는 최소 0 ACU Auto-pause를 제공한다.

### 남은 문제

- Staging EC2·Domain·Secret·DB의 실제 구성이 아직 없다.
- User Journey Account Pool과 보낸·받은 선물, 친구, 조회 이력 Fixture가 아직 없다.
- Fixture Reset·Cleanup·Snapshot Restore가 아직 검증되지 않았다.
- Ephemeral RDS의 Instance Class·유지 시간·비용 상한이 정해지지 않았다.

### 다음 작업

1. V1 Staging의 Domain·EC2·MySQL Volume·Secret 경계를 확정한다.
2. Staging Compose와 Health·Smoke 경로를 구성한다.
3. Light·Normal·Heavy Account와 관계형 Fixture 생성·Reset 절차를 작성한다.
4. API·User Journey Smoke와 Baseline을 실행한다.
5. 첫 결과가 쌓인 뒤 Prometheus·Grafana 시각화를 구성한다.

## 2026-09-28 API별 부하 테스트 1~4단계 완료

### 완료한 작업

- Backend `main` 구현을 기준으로 핵심 API의 인증·데이터 변경·운영 위험도를 분류했다.
- k6의 설정, HTTP 요청, Metric과 Profile을 공통 모듈로 분리했다.
- 상품·선물·친구·사용자·인증 영역의 독립 API 스크립트 13개를 추가했다.
- `smoke`, `baseline`, `capacity`, `breakpoint` 네 Profile을 표준화했다.
- Capacity와 Breakpoint는 운영 Domain에서 항상 차단하고, 데이터 변경 API는 Profile과 무관하게 운영에서 차단했다.
- Breakpoint는 Staging에서 30 RPS부터 500 RPS까지 단계적으로 증가하도록 구성했다.
- 기존 `load-tests/k6/products-list.js` 경로는 새 상품 목록 스크립트의 호환 Entry Point로 유지했다.

### 검증 결과

- 14개 Entry Point를 `k6 inspect --execution-requirements`로 검증했다.
- 네 표준 Profile과 기존 `load` 호환 Alias의 Executor, RPS Stage와 최대 VU를 확인했다.
- 운영 Domain에서 `capacity`가 차단되고, 상품 상세 Smoke도 DB Side Effect 때문에 차단되는 것을 확인했다.
- `k6 inspect`는 기본적으로 System Environment Variable을 전달하지 않아 `-e KEY=VALUE`가 필요함을 확인했다.

### 이번 결정

- Candidate와 Release는 동일 Docker Hub Repository에서 관리한다.
- Candidate는 `develop-{commit-sha}`, Release는 Semantic Version Tag를 사용한다.
- Staging 검증 후 Source를 재빌드하지 않고 동일 Image Digest에 Release Tag를 부여한다.
- Mac이 500 RPS를 만들지 못한다고 가정하지 않는다. 실제 시험에서 목표 RPS, `dropped_iterations`, Mac CPU·Memory·Network를 함께 측정해 부하 발생기 병목 여부를 판정한다.

### 다음 작업

1. Staging 인프라와 운영과 분리된 DB·Secret을 구성한다.
2. FE·BE `develop` Candidate Build와 동일 Docker Hub Repository Push를 구현한다.
3. Staging 자동 배포, Health·Smoke와 이전 Digest Rollback을 구현한다.
4. Fixture와 Reset 절차, APM을 준비한 뒤 API Smoke·Baseline부터 실행한다.
5. Mac 부하 발생기 검증 후 Capacity·Breakpoint를 실행한다.

## 2026-09-28 Staging 자동 배포·Image 승격 정책 결정

### 결정

- FE·BE `develop` Merge 시 Candidate Image를 만들고 Staging에 자동 배포한다.
- Candidate와 Release는 동일 Docker Hub Repository에서 Tag로 구분한다.
- 별도 Docker Hub Candidate Repository와 Amazon ECR은 현재 단계에서 채택하지 않는다.
- Candidate Tag는 Commit SHA 기반으로 만들고 덮어쓰지 않는다.
- Staging에서 검증한 동일 Image Digest만 Docker Hub의 Semantic Version Release로 승격한다.
- Production 배포는 Cloud Repository의 수동 실행과 승인 절차를 유지한다.
- 정식 부하 시험 중에는 FE·BE Digest를 고정하고 Staging 자동 배포를 일시 동결한다.

### 필요한 후속 작업

1. Docker Hub Candidate Tag의 보존기간과 정리 방식을 확정한다.
2. FE·BE CI에 `develop` Candidate Build·기존 Repository Push를 추가한다.
3. Cloud Repository에 Staging 자동 배포·Smoke·Rollback Workflow를 추가한다.
4. 검증된 Candidate Digest를 Docker Hub Release로 승격하는 Workflow를 설계한다.
5. 부하 시험 중 배포 동결과 해제 절차를 구현한다.

## 2026-09-28 API 위험도 분류·Staging 요구사항 준비

### 완료한 작업

- Backend `main`의 Controller와 Service를 기준으로 외부 API의 인증·데이터 변경·부하 테스트 위험도를 분류했다.
- 모든 GET이 읽기 전용이라는 기존 가정을 수정하고 상품 상세 조회의 DB 변경 동작을 확인했다.
- FE·BE·Cloud 세 Repository의 Image 발행 및 CD Trigger를 비교했다.
- 초기 Staging은 `develop` 자동 배포가 아니라 검증된 FE·BE Version 쌍의 수동 승격 방식으로 시작하기로 제안했다.
- Staging 인프라, Database·Fixture, APM·모니터링과 완료 조건을 문서화했다.

### 검증한 항목

- `GET /products/{productId}`는 `product_views` 기록과 상품 조회 수 증가를 수행할 수 있다.
- 로그인·Refresh·Logout은 Session 또는 로그인 제한 상태를 변경한다.
- FE·BE CI는 `main` Push에서만 Version Image를 발행하며 `develop` Merge Image를 발행하지 않는다.
- Cloud에는 수동 Production CD만 있고 Staging CD는 없다.
- Backend `1.1.0`의 필수 AI 환경변수와 Cloud `main` Compose가 현재 일치하지 않는다.
- Backend Flyway `V9__reset_product_catalog.sql`은 기존 상품·선물 관련 데이터를 삭제한다.

### 정정 사항

- 기존 문서의 "인증 Journey는 GET 요청만 사용하므로 Business Data를 생성하지 않는다"는 설명은 상품 상세 조회 구현과 맞지 않는다.
- 상품 상세 단계는 조회 이력과 조회 수를 변경할 수 있으므로 이후 전체 인증 Journey는 Staging 전용으로 취급한다.

### 다음 작업

1. API 위험도 목록을 BE팀과 검토하고 공개 상품 목록과 Preflight의 의도를 확인한다.
2. Cloud 작업 브랜치의 AI 환경변수 변경을 검증해 `main`에 반영한다.
3. Staging 배포용 Compose Overlay와 수동 CD Workflow를 설계한다.
4. Staging 전용 Secret·DB·Fixture·Reset 절차를 확정한다.
5. APM 적용 후 API별 Baseline·Capacity Test를 실행한다.

## 2026-09-28 AI 미배포 상태의 FE·BE 배포 준비

### 완료한 작업

- Backend `1.1.0`의 필수 AI 설정을 Cloud Compose에서 전달하도록 추가했다.
- AI 서비스가 배포되기 전 프로파일링 스케줄러를 기본 비활성화하도록 `SCHEDULING_ENABLED=false`를 적용했다.
- `.env.example`과 README에 AI 미배포 상태의 설정·제약·Secret 관리 원칙을 기록했다.
- Git에서 제외된 로컬 `.env`에는 비운영 AI Placeholder와 `SCHEDULING_ENABLED=false`를 반영했다.
- 컨테이너 간 AI 주소는 `localhost`가 아닌 Compose Service Name 기반 `http://ai:8000`을 사용하도록 정리했다.

### 검증한 항목

- `.env.example`의 비운영 Placeholder로 Production Compose Configuration 검증이 통과했다.
- `AI_PROFILE_BASE_URL`을 비우면 필수 변수 오류로 Compose Configuration 검증이 실패했다.
- `PROFILING_SERVICE_TOKEN`을 비우면 필수 변수 오류로 Compose Configuration 검증이 실패했다.
- 배포·SSM·검증 Shell Script의 Bash 문법 검사가 통과했다.
- 실제 Backend Container Health Check는 Flyway의 운영 데이터 변경을 피하기 위해 실행하지 않았다.

### 운영 적용 전 차단 조건

- 운영 EC2 `.env`에 `AI_PROFILE_BASE_URL`, `PROFILING_SERVICE_TOKEN`, `SCHEDULING_ENABLED=false`를 반영해야 한다.
- Backend `V9__reset_product_catalog.sql`의 운영 데이터 삭제가 승인되지 않았고 DB Backup도 아직 확인되지 않았다.
- 자동 Rollback은 Container Image만 복구하며 Flyway가 변경한 DB Data를 복구하지 않는다.

### 다음 작업

1. 운영 DB Migration 적용 여부와 데이터 초기화 의도를 Backend 담당자에게 확인한다.
2. 운영 DB Backup과 복구 가능성을 확인한다.
3. 운영 EC2의 Compose File과 `.env`를 갱신한다.
4. FE·BE `1.1.0` Production CD를 실행하고 Backend Health와 외부 Smoke Test를 확인한다.
5. AI Repository CI와 AI Service 배포는 후속 작업으로 구성한다.

## 2026-09-28 M5 SLI/SLO 설계 업데이트

### 완료한 작업

- 사용자가 실제로 경험하는 서비스 접근, 핵심 API 응답시간, 인증 읽기 Journey를 V1 SLI로 정의했다.
- 가용성 99.5%, 핵심 읽기 API `p95 500ms`·`p99 1,000ms`, Journey 성공률 99.0%를 잠정 SLO로 정했다.
- 각 SLI의 측정 위치, 분모·분자, 제외 조건, 집계 기간과 현재 측정 가능 상태를 명시했다.
- 28일 오류 예산과 소진 단계별 배포·안정성 정책, 빠른 소진 신호를 정의했다.
- SLO와 Health Check, 자원 지표, k6 Release Gate의 역할을 분리했다.
- Nginx Log, CloudWatch, Synthetic Check, Dashboard·Alarm의 구현 순서와 완료 증거를 정했다.

### 검증한 항목

- 현재 Nginx 설정에는 SLI 집계에 필요한 구조화 Access Log Format이 없다.
- Backend Actuator는 Health만 노출하고 있어 JVM·HTTP·Hikari 지표는 현재 수집할 수 없다.
- 현재 운영 API 기준으로 로그인, 상품 탐색, 보낸/받은 선물 이력 Journey를 구성할 수 있다.
- 선물 생성·맞춤 추천은 운영 Controller가 완성되지 않아 현재 SLO 범위에서 제외해야 한다.
- 기존 상품 목록 부하 테스트 수치는 Release Gate의 근거일 뿐 28일 SLO 달성 증거가 아니다.

### 남은 문제

- SLI는 설계됐지만 Nginx Log, CloudWatch 집계와 Synthetic Monitor가 아직 구현되지 않았다.
- 정상 4xx와 제품 결함 4xx를 구분할 Backend 오류 코드 분류가 필요하다.
- 선물 상세가 있는 Synthetic 전용 계정과 Fixture가 필요하다.
- Product·Frontend·Backend 담당자의 SLO 범위와 잠정 목표 승인이 필요하다.

### 다음 작업

1. Nginx 구조화 Access Log와 Request ID를 구현하고 표본 Log를 검증한다.
2. CloudWatch Logs 전송·보존과 SLI Query를 구성한다.
3. Frontend 및 인증 핵심 Journey Synthetic Check를 구축한다.
4. SLI Dashboard와 장애 알림을 연결해 M7의 입력으로 사용한다.
5. 2026-10-12 데이터 품질을 점검하고 2026-10-26에 잠정 SLO를 보정·확정한다.

- 마지막 업데이트: 2026-09-28
- 현재 단계: M5 SLI/SLO 설계 완료, 측정 구현 준비
- 상태: 진행 중

## 이번에 완료한 작업

- 저장소 루트의 `AGENTS.md`에 지속 적용할 작업 규칙 정리
- 프로젝트의 사실, 제공된 트래픽 가정, 설계 제약을 `PROJECT_CONTEXT.md`에 분리
- V2 컴퓨팅 방식의 문제, 평가 기준, 대안, 미확인 항목을 ADR로 기록
- 이번에 생성한 로컬 AI 컨텍스트 문서를 Git 추적 대상에서 제외

## 검증한 내용

- Docker Compose에 Frontend, Backend, MySQL 서비스가 정의되어 있다.
- Backend와 MySQL에 health check가 정의되어 있다.
- Nginx가 정적 파일을 제공하고 `/api/` 요청을 Backend로 전달한다.
- Production 오버레이는 세 서비스에 `restart: unless-stopped`를 적용한다.
- `.gitignore`가 `AGENTS.md`와 `docs/` 전체를 제외한다.

## 확정된 결정

- 600 RPS에 필요한 태스크 수나 인스턴스 수는 현재 확정하지 않는다.
- 최종 용량은 관측 데이터와 부하 테스트를 통해 결정한다.
- 비용, 운영 난이도, 장애 대응, 확장성을 함께 비교한다.
- 현재 단계에서는 특정 V2 컴퓨팅 방식을 확정하지 않는다.

## 보류된 결정

- EC2 직접 운영, EC2 + ASG, ECS on EC2, ECS on Fargate 중 최종 방식
- 데이터베이스의 분리 및 고가용성 방식
- 다중 AZ 적용 시점
- V2의 태스크·인스턴스 수와 Auto Scaling 기준

## 발견된 문제

- 현재 구성에는 자동 확장과 다중 인스턴스 구성이 없다.
- 애플리케이션과 데이터베이스 장애를 호스트 수준에서 격리하는 구성이 문서화되어 있지 않다.
- 부하 테스트 결과와 실제 자원 사용량이 없다.
- 목표 복구 시간과 허용 가능한 배포 중단 시간이 정의되어 있지 않다.

## 열린 질문

- ECS 도입으로 해결하려는 핵심 운영 문제는 무엇인가?
- ASG 기반 EC2 운영으로 V2 요구사항을 충족할 수 있는가?
- Fargate의 추가 비용을 정당화할 운영상 이점이 있는가?
- 데이터베이스를 언제, 어떤 방식으로 애플리케이션 호스트에서 분리할 것인가?

## 다음 작업

1. 복구 시간, 배포 중단 허용 범위, 확장 목표를 요구사항으로 정의한다.
2. 대안별 운영 책임과 배포·확장·장애 대응 방식을 같은 기준으로 비교한다.
3. 동일한 워크로드 조건으로 비용 계산 기준을 정의한다.
4. 관측 항목과 부하 테스트 시나리오를 작성한다.
5. 측정 결과를 근거로 조건부 결론을 기록한다.

## 주의사항

- 측정하지 않은 성능 수치를 확정값처럼 사용하지 않는다.
- 제공된 트래픽 수치는 검증된 측정값이 아니라 설계 가정으로 취급한다.
- 기술 장점보다 해결할 문제와 판단 기준을 먼저 문서화한다.

## 2026-09-26 작업 업데이트

### 완료한 작업

- 부하 테스트 README를 입문자 관점에서 전면 재구성했다.
- k6 선택 근거, 시나리오 구조, VU·iteration·RPS 차이와 각 Threshold 의미를 문서화했다.
- Smoke, Baseline, Load 1·2차의 실제 수치를 의미와 함께 기록했다.
- k6 Web Dashboard HTML export와 JSON 결과 보존 방법을 추가했다.
- V1 SLI 정의, 잠정 SLO, Release Gate와 운영 데이터 기반 확정 일정을 `load-tests/SLI-SLO.md`에 작성했다.

### 확정한 원칙

- k6 결과는 배포 전 성능 증거로 사용하되 28일 운영 SLO 달성 증거로 해석하지 않는다.
- 2026-09-28 출시 전 SLI 정의와 잠정 SLO를 승인하고, 2026-10-12와 2026-10-26에 보정·확정한다.
- Portainer는 운영 UI로 사용할 수 있지만 SLI 이력·경보를 담당하는 주 모니터링 도구로 간주하지 않는다.

### 다음 작업

1. Nginx Access Log의 request time과 upstream response time 기록 여부를 확인한다.
2. CloudWatch Agent, Logs, Alarm과 외부 Health Check의 최소 관측 구성을 구현한다.
3. 절전 영향을 제거한 AWS 부하 발생기에서 30 RPS 시험을 재현한다.
4. 상품 상세, 로그인과 선물 여정의 SLI 및 부하 시나리오를 추가한다.

## 2026-09-26 Portainer·부하 테스트 시각화 업데이트

### 완료한 작업

- Portainer를 애플리케이션 배포와 분리한 `operations/portainer/compose.yaml`을 추가했다.
- Portainer 9443을 Host Loopback에만 Binding하고 Host Nginx에서 관리자 IP를 제한하는 Template을 추가했다.
- `portainer.seonjalal.com` Route 53 A Record 변경 Template을 추가했다.
- DNS, Nginx, Certbot, 최초 관리자 설정, 보안 검증, Rollback 과정을 `operations/portainer/README.md`에 문서화했다.
- k6 Web Dashboard의 실시간 Browser 시각화와 HTML 자동 Export 명령을 부하 테스트 README에 추가했다.

### 검증한 항목

- 현재 로컬 AWS CLI 계정은 `155359186350`으로, 운영 대상 계정 `721744297924`과 다르다.
- 현재 계정에는 `seonjalal.com` Hosted Zone과 운영 EC2 `i-0417358a95b551c12`가 없다.
- 잘못된 계정 변경을 막기 위해 실제 Route 53 및 EC2 변경은 실행하지 않았다.
- Portainer는 기존 Production CD의 FE·BE 배포 및 Rollback 대상에 포함하지 않았다.

### 남은 문제

- 운영 AWS 계정 자격 증명으로 전환해야 한다.
- Portainer Nginx Allowlist에 사용할 관리자 Public IPv4를 확정해야 한다.
- 운영 Docker Engine 25.0.16과 Portainer 2.45.1 LTS 호환성을 비운영 환경에서 먼저 확인해야 한다.
- Portainer Live Stats는 장기 시계열을 보존하지 않으므로 반복 시험 비교에는 별도 Metrics 저장소가 필요하다.

### 다음 작업

1. 운영 계정 `721744297924` Identity와 Hosted Zone·EC2 상태를 재확인한다.
2. 승인된 관리자 IP로 Nginx Template을 완성한다.
3. Route 53, TLS, Loopback Portainer 순서로 배포하고 5분 안에 최초 관리자 설정을 완료한다.
4. Smoke/Baseline 실행 중 Portainer Stats와 k6 Dashboard의 동일 시간대 지표를 비교한다.

## 2026-09-28 인증 사용자 여정 부하 테스트 준비

### 완료한 작업

- 로그인 필수 서비스 정책에 맞춰 비로그인 사용자 여정을 제외했다.
- Access Token을 환경변수로 주입하는 인증된 읽기 전용 k6 여정을 추가했다.
- 상품 카테고리·목록·상세와 보낸/받은 선물 목록·상세의 응답을 연결해 검증한다.
- 사용자 Think Time, 여정 전체 성공률과 여정 전체 소요시간 지표를 추가했다.
- Smoke 1회와 가설 기반 3 VU·5분 Baseline만 허용하고 운영 Baseline에는 명시적 허용값을 요구한다.
- 로그인 Session과 쓰기 데이터의 보존·정리 원칙을 부하 테스트 문서에 기록했다.

### 검증한 항목

- k6 `inspect`로 Script 초기화, Scenario와 Threshold 구성을 확인했다.
- 현재 로그인은 호출 시 `user_sessions`에 Refresh Token Hash Session을 생성할 수 있다.
- Access Token 유효기간은 1시간이므로 짧은 조회 Baseline에서는 로그인 반복이 필요하지 않다.
- 인증 여정은 GET 요청만 사용하므로 상품·선물·사용자 Business Data를 생성하지 않는다.

### 남은 문제

- 전용 Test Account와 충분한 상품·보낸 선물·받은 선물 Fixture가 아직 확정되지 않았다.
- 운영 Access Log 기반 실제 사용자 여정 비중과 동시 사용자 수가 아직 없다.
- 만료된 `user_sessions`를 정리하는 Backend Scheduled Cleanup 정책이 확인되지 않았다.
- 쓰기 API는 Run ID 식별, Cleanup 순서, Snapshot 복구가 준비되기 전까지 부하 테스트 대상에서 제외한다.

### 다음 작업

1. 전용 Test Account로 인증 조회 Smoke를 실행한다.
2. Smoke 결과에서 API Contract와 권한 설정을 확인한다.
3. 승인된 시간에 3 VU·5분 Baseline을 실행하고 HTML 결과와 Server 지표를 저장한다.
4. Access Log를 바탕으로 API 비중과 Journey Load Profile을 보정한다.
5. Backend에 만료 Session 보존 기간과 Cleanup Job을 설계한다.

### 첫 인증 조회 Smoke 결과

- 1 VU·1 Journey가 6.6초에 완료됐다.
- HTTP 요청 5개, Check 10개, Journey 성공률이 모두 100%였다.
- HTTP 실패와 Business 오류는 0%였다.
- 상품 카테고리·목록·상세, 보낸 선물 목록, 받은 선물 목록을 검증했다.
- Test Account의 보낸/받은 선물 목록이 비어 있어 두 선물 상세 분기는 실행되지 않았다.
- 선물 상세 분기 실행률을 보여주는 Coverage 지표와 `REQUIRE_GIFT_DETAILS=true` 엄격 모드를 추가했다.
- 전체 읽기 여정 Baseline 전에 선물 이력이 있는 Test Account로 엄격 Smoke를 실행해야 한다.

### Team 공유 문서 준비

- 상품 목록 Smoke·Baseline·Load 1·2차와 인증 Journey Smoke를 하나의 Team 공유 문서로 통합했다.
- Tail Latency, Payload, 이상치, 실행 환경과 사용자 정책·Security 설정 차이를 위험 후보로 정리했다.
- Backend Team에 Test Account·Fixture·Session Cleanup·Staging Reset·Server Metrics 협업을 요청할 항목을 정리했다.
- Spike·Stress는 한 계정으로 실행 가능하지만 실제 사용자 결과로 해석하려면 최대 동시 VU에 맞는 Account Pool이 필요하다는 원칙을 기록했다.
- 결과 문서는 `load-tests/results/2026-09-28-team-load-test-summary.md`에 로컬 전용으로 보존한다.

## 2026-09-17 작업 업데이트

### 완료한 작업

- FE·BE Docker 이미지와 애플리케이션 CI 구성이 각 저장소의 `develop` 브랜치에 반영되었다.
- PR 단계에서 애플리케이션 테스트, Build, Docker Build, Smoke Test를 수행하도록 구성했다.
- Docker Hub Publish는 `main` Push와 Validate 성공 조건을 모두 만족할 때만 실행하도록 구성했다.
- Docker 및 CI 작업 과정, 트러블슈팅, 핵심 개념을 `2026-09-17-docker-image-and-ci-worklog.md`에 기록했다.

### 검증한 항목

- FE·BE PR의 GitHub Actions 통과와 팀 리뷰 완료를 확인했다.
- Docker Hub Repository Secrets 이름과 Workflow 참조가 일치함을 확인했다.
- Backend Health 응답이 인증 없이 HTTP 성공 및 `status: UP`인지 검사하도록 구성했다.

### 남은 문제

- 애플리케이션 개발 중이므로 `main` Publish와 Docker Hub `1.0.0` 이미지 발행은 아직 실행하지 않았다.
- Cloud 레포의 Docker Compose 초안은 실제 FE·BE·MySQL 통합 실행 검증이 필요하다.
- EC2 대상 Docker Compose CD는 아직 구현하지 않았다.

### 다음 작업

1. 로컬 이미지로 Docker Compose 통합 실행을 검증한다.
2. Cloud 레포의 Compose CI를 구성한다.
3. EC2 환경과 Secret 관리 방식을 결정한 뒤 Cloud CD를 구성한다.

## 2026-09-18 작업 업데이트

### 완료한 작업

- Docker Hub 인증 정보를 Repository Secrets와 Environment Secrets 중 어디에 저장할지 검토했다.
- 현재 FE·BE Publish 구조에는 Repository Secrets를 사용하는 것으로 결정했다.
- 문서 전용 변경에서 CI가 불필요하게 실행되는 원인과 `paths-ignore` 해결 방법을 문서화했다.

### 검증한 항목

- FE·BE Publish Job은 `main` Push에서만 실행된다.
- Docker Hub Repository Secrets는 PR Validate에서는 사용되지 않는다.
- Markdown과 `docs/**`만 변경된 경우 FE·BE CI가 실행되지 않도록 설정되어 있다.

### 남은 문제

- Docker Compose 기반 통합 실행은 아직 검증하지 않았다.
- Cloud CI와 EC2 대상 CD는 아직 구현하지 않았다.

### 다음 작업

1. 로컬 FE·BE 이미지를 Build한다.
2. Docker Compose로 FE·BE·MySQL을 함께 실행한다.
3. Compose 검증 결과를 바탕으로 Cloud CI/CD 구성을 진행한다.

## 2026-09-19 작업 업데이트

### 완료한 작업

- BE·FE·Cloud 세 저장소의 원격 `develop`을 fetch하고 로컬 브랜치와 비교했다.
- 각 저장소의 파일 구조, 현재 구현 기능, Dockerfile, CI workflow를 코드 수준에서 검토했다.
- 저장소 간 환경 변수, API proxy, 이미지 발행 방식의 통합 차이를 문서화했다.
- 상세 결과를 `2026-09-19-develop-sync-and-code-review.md`에 기록했다.

### 검증한 항목

- BE 현재 작업 브랜치는 원격 `develop`보다 46커밋 뒤이고, 로컬 `develop`은 47커밋 뒤다.
- FE·Cloud 현재 작업 브랜치의 파일 트리는 원격 `develop`과 동일하며 merge commit만 뒤에 있다.
- Cloud Compose configuration과 `scripts/verify.sh` Bash 문법이 유효하다.
- 최신 BE가 요구하는 S3·JWT·CORS 환경 변수가 Cloud Compose에 아직 없다.
- Cloud Nginx 일반 `/api/` proxy가 BE의 현재 API 경로와 일치하지 않는다.
- 작업 규칙이 참조하는 `docs/ARCHITECTURE.md`가 현재 존재하지 않는다.

### 남은 문제

- 세 저장소 로컬 `develop`의 실제 fast-forward는 아직 수행하지 않았다.
- 최신 FE·BE develop 이미지의 Compose 통합 실행은 아직 검증하지 않았다.
- BE Secret 실제 값과 저장 위치는 아직 확정되지 않았다.
- 로그인·Refresh Token API가 아직 없어 보호된 BE API의 전체 인증 흐름을 검증할 수 없다.
- 운영 DB migration과 Spring Profile 전략이 정해지지 않았다.

### 다음 작업

1. 로컬 미커밋 변경을 보존하고 세 저장소 `develop`을 fast-forward한다.
2. Cloud Compose에 최신 BE 환경 변수 이름을 추가한다.
3. Nginx `/api/` prefix 처리 방식을 수정하고 공개 API routing을 검증한다.
4. 최신 develop 소스로 FE·BE 로컬 이미지를 빌드해 Compose 통합 실행한다.
5. 배포 방식 확정 후 Secret 저장 위치를 결정한다.

## 2026-09-20 작업 업데이트

### 완료한 작업

- BE·FE·Cloud 세 저장소를 로컬 `develop` 브랜치로 전환했다.
- 각 저장소에서 `git pull --ff-only origin develop`로 원격 최신 커밋을 반영했다.
- BE와 Cloud의 기존 미커밋 변경을 stash로 보관한 뒤 새 `develop` 위에 복원했다.

### 검증한 항목

- 세 저장소 모두 로컬 `HEAD`와 `origin/develop` 커밋이 일치한다.
- 세 저장소 모두 원격 대비 ahead/behind가 `0/0`이다.
- stash 복원 과정에서 충돌이 발생하지 않았다.
- 기존 BE 테스트 파일 변경과 Cloud `.gitignore` 변경이 보존됐다.

### 남은 문제

- Cloud Compose에 최신 BE의 S3·JWT·CORS 환경 변수가 아직 없다.
- Nginx `/api/` proxy prefix와 BE API 경로가 아직 일치하지 않는다.
- 최신 FE·BE develop 소스의 Compose 통합 실행은 아직 검증하지 않았다.

### 다음 작업

1. Cloud Compose와 `.env.example`에 최신 BE 환경 변수 이름을 추가한다.
2. Nginx `/api/` prefix 처리 방식을 정리한다.
3. 최신 FE·BE 로컬 이미지를 빌드해 Compose 통합 실행한다.

## 2026-09-21 작업 업데이트

### 완료한 작업

- BE 원격 `develop`의 PR #104 및 Testcontainers 전환 커밋 `ed76830`을 검토했다.
- BE 이슈 #107에 따라 최신 `develop`에서 `chore/#107-backend-ci-testcontainers` 브랜치를 생성했다.
- Backend CI `validate` job에서 미사용 MySQL service와 `DB_URL`, `DB_USERNAME`, `DB_PASSWORD`를 제거했다.
- GitHub runner의 Docker socket과 daemon 접근을 확인하는 진단 step을 추가했다.
- BE CI 변경을 `f712b07`로 로컬 커밋했다.
- 상세 작업 기록을 `2026-09-21-testcontainers-ci-cleanup-worklog.md`에 작성했다.

### 검증한 항목

- 스모크 테스트용 MySQL 컨테이너와 DB 환경 변수는 변경하지 않았다.
- `AWS_REGION`, `PRODUCT_IMAGE_BUCKET`은 테스트 설정이 참조하므로 유지했다.
- Java 25 Linux 컨테이너와 Docker socket을 이용해 Testcontainers 전체 테스트 380개가 통과했다.
- 첫 로컬 중첩 실행의 Ryuk 연결 실패가 Docker Desktop host 주소 차이임을 확인하고 로컬 실행에만 `TESTCONTAINERS_HOST_OVERRIDE`를 적용했다.
- Workflow YAML parse와 `git diff --check`가 통과했다.

### 남은 문제

- 실제 GitHub `ubuntu-latest`에서 PR workflow 통과를 확인해야 한다.
- Docker Build와 애플리케이션 Smoke Test 결과를 확인해야 한다.
- 변경 전후 CI 실행 시간은 아직 비교하지 않았다.
- 원격 Push 승인 요청이 거부되어 작업 브랜치 Push와 PR 생성은 수행하지 못했다.

### 다음 작업

1. Push 승인이 가능해지면 이슈 #107 브랜치를 원격에 Push한다.
2. `develop` 대상 PR을 생성한다.
3. GitHub Actions의 Testcontainers 테스트, Docker Build, Smoke Test 결과를 확인한다.
4. CI 결과와 PR 정보를 작업 문서에 갱신한다.

## 2026-09-22 작업 업데이트

### 완료한 작업

- Wiki의 V1 CI 설계와 현재 FE·BE workflow를 항목별로 비교했다.
- Cloud 레포에 수동 운영 배포용 `.github/workflows/cd-production.yml` 초안을 작성했다.
- CD 초안에 FE·BE 버전 입력, 이미지 존재 확인, 배포 동시 실행 방지, EC2 SSH 배포, Health Check, Smoke Test와 실패 시 Rollback 흐름을 반영했다.

### 검증한 항목

- FE·BE의 PR 검증과 `main` Push 이미지 발행은 Wiki의 핵심 흐름을 충족한다.
- Wiki가 요구하는 `develop` Push 통합 검증 Trigger는 현재 FE·BE workflow에 없다.
- 인프라 Repository의 Compose 통합 Gate가 아직 없다.
- CD workflow YAML parse, 원격 배포 Shell 구문 검사와 `git diff --check`가 통과했다.

### 남은 문제

- GitHub-hosted Runner가 운영 EC2에 SSH로 접근할 네트워크 경로가 확정되지 않았다.
- 최신 BE 필수 환경 변수와 Nginx `/api` prefix 문제가 Compose에 남아 있다.
- 최신 FE·BE 이미지의 Compose 통합 실행을 완료하지 않았다.
- 운영 EC2의 `/opt/seonjalal` 초기 구성과 GitHub Secrets·Variable 등록이 필요하다.
- 실제 운영 배포와 Rollback은 실행하지 않았다.

### 다음 작업

1. Compose 환경 변수와 Nginx routing을 최신 애플리케이션 계약에 맞춘다.
2. 일시적인 Compose 통합 CI를 구성하고 최신 이미지 조합을 검증한다.
3. EC2 SSH 접속 방식을 고정 출발지 Runner 또는 제한된 수동 실행 중에서 결정한다.
4. 결정된 접근 방식에 맞춰 CD workflow Secret을 등록한다.
5. 테스트 EC2 또는 점검 시간에 최초 배포와 Rollback을 검증한다.

## 2026-09-23 작업 업데이트

### 완료한 작업

- FE 운영 Docker 빌드가 `VITE_USE_MOCK_API=false`와 `VITE_API_BASE_URL=/api`를 명시적으로 사용하도록 구성했다.
- 필수 Vite build argument가 없거나 mock 모드이면 FE Docker 빌드가 실패하도록 검증을 추가했다.
- Nginx가 `/api/` prefix를 제거한 뒤 Backend로 전달하도록 `proxy_pass` URI를 수정했다.

### 검증한 항목

- 운영 build argument를 전달한 FE Docker 이미지가 정상적으로 빌드됐다.
- build argument를 생략한 FE Docker 빌드가 의도대로 실패했다.
- 빌드된 정적 번들에 `/api` 기준 경로가 포함된 것을 확인했다.
- Docker Compose configuration과 실제 Nginx 이미지의 설정 문법 검사가 통과했다.

### 남은 문제

- 실제 운영 Backend와 계정 정보를 이용한 로그인 및 실데이터 조회는 아직 검증하지 않았다.
- 변경된 FE 이미지를 Docker Hub에 발행하고 EC2에서 Compose로 통합 실행해야 한다.

### 다음 작업

1. FE 변경사항을 리뷰하고 `develop`, `main` 순서로 반영한다.
2. 새 FE 이미지를 발행한 뒤 Cloud Compose에서 FE·BE 통합 실행을 검증한다.
3. 실제 계정 로그인과 주요 화면의 실서버 데이터 조회를 확인한다.

## 2026-09-23 OIDC·SSM CD 전환 업데이트

### 완료한 작업

- Production CD의 GitHub-hosted Runner 직접 SSH 접속을 제거했다.
- GitHub OIDC 단기 자격 증명과 특정 EC2 대상 SSM Run Command 방식으로 배포 경로를 변경했다.
- 기존 원격 배포·Health Check·Smoke Test·Rollback 로직을 `scripts/deploy-production.sh`로 분리했다.
- 배포 스크립트를 압축·인코딩해 SSM으로 전달하고 실행 상태와 로그를 회수하는 `scripts/deploy-production-via-ssm.sh`를 추가했다.
- 필수 GitHub Repository Variables와 EC2 사전 조건을 README에 기록했다.

### 검증한 항목

- 두 배포 스크립트의 Bash 구문 검사가 통과했다.
- Production CD workflow YAML 파싱과 `git diff --check`가 통과했다.
- AWS 호출 모의 실행으로 SSM parameters JSON, Command ID 처리, 완료 상태 폴링과 stdout 출력 흐름을 검증했다.
- 압축·Base64 인코딩된 원격 배포 스크립트가 약 2.7KB임을 확인했다.
- 분리된 배포 스크립트의 핵심 배포·Rollback 로직이 기존 SSH heredoc과 동일함을 비교했다.

### 남은 문제

- 변경사항을 `develop`, `main` 순서로 반영해야 한다.
- 실제 GitHub OIDC 역할 수임과 SSM Run Command 실행은 main 반영 후 검증해야 한다.
- S3의 `products/1/main.jpg` 운영 키가 존재하지 않거나 현재 역할로 확인할 수 없어 상품 이미지 경로를 정리해야 한다.
- SSM CD 성공 후 기존 GitHub SSH Secrets를 삭제해야 한다.

### 다음 작업

1. 변경사항을 리뷰하고 작업 브랜치에 커밋한다.
2. `develop`, `main` 순서로 PR을 반영한다.
3. Production CD를 다시 실행해 OIDC 수임, SSM 명령, FE·BE 배포와 외부 Smoke Test를 확인한다.
4. 성공 후 GitHub의 기존 Production SSH Secrets를 제거한다.

### OIDC·SSM 최초 실행 후 수정

- GitHub OIDC 역할 수임과 SSM SendCommand 호출이 성공했다.
- 원격 래퍼가 임시 파일 경로에 따옴표 문자를 포함해 배포 스크립트 실행 전에 실패하는 문제를 확인했다.
- `scripts/deploy-production-via-ssm.sh`에서 불필요한 따옴표 이스케이프를 제거했다.
- 생성된 SSM 명령을 실제로 디코딩하고 임시 스크립트를 생성하는 모의 실행으로 수정 사항을 검증했다.

## 2026-09-23 V1 최초 운영 배포 완료 업데이트

### 완료한 작업

- Amazon Linux 2023 EC2에 Docker와 Docker Compose 실행 환경을 구성했다.
- MySQL 8.4를 Compose `database` 서비스로 실행하고 `seonjalal_db-data` named volume을 생성했다.
- EC2에 SSM 및 S3 상품 이미지 읽기 권한을 가진 Instance Role을 연결했다.
- 운영 `.env`에서 정적 AWS Access Key를 제거하고 EC2 IAM Role 사용을 검증했다.
- Route 53의 `www.seonjalal.com`을 운영 EC2에 연결하고 HTTPS를 적용했다.
- GitHub-hosted Runner의 직접 SSH 배포를 GitHub OIDC + SSM Run Command 방식으로 전환했다.
- SSM 임시 배포 스크립트 경로의 잘못된 quoting을 수정했다.
- Production CD로 FE `1.0.0`, BE `1.0.0` 배포에 성공했다.

### 검증한 항목

- GitHub OIDC Role 수임과 AWS Identity 확인이 성공했다.
- SSM Command가 Production EC2에서 배포 스크립트를 실행했다.
- Frontend, Backend, MySQL Container가 모두 Healthy 상태다.
- `https://www.seonjalal.com/`이 Frontend HTML을 반환한다.
- `/api/health`가 HTTP 200과 `status: UP`을 반환한다.
- `/api/products?sort=POPULAR`과 `sort=MOST_GIFTED`가 HTTP 200을 반환한다.
- Release Environment File 권한이 `600`이다.
- EC2 IAM Role로 S3 테스트 객체를 읽을 수 있다.

### 발견된 문제

- DB의 이미지 Key는 `products/{id}/main.jpg`이지만 실제 S3 객체는 `test/products/<UUID>-768.avif` 형식이라 상품 이미지가 표시되지 않는다.
- Frontend 기본 정렬 `AI_RECOMMENDED`는 Backend에서 지원하지 않아 상품 화면 첫 요청이 400으로 실패한다.
- Frontend 구매순 `PURCHASED`와 Backend `MOST_GIFTED`가 불일치해 구매순 요청이 400으로 실패한다.
- Public IP 직접 접속의 Nginx 404는 정적 artifact 누락이 아니라 `Host`와 `server_name` 불일치다.

### 다음 작업

1. Frontend 기본 정렬을 `POPULAR`로 변경하고 미지원 AI 추천순을 임시로 숨긴다.
2. Frontend 구매순 Mapping을 `MOST_GIFTED`로 변경한다.

3. S3 상품-UUID 이미지 매핑 자료를 확보한다.
4. 새 Flyway Migration으로 `product_images.object_key`를 실제 S3 Key와 일치시킨다.
5. 수정된 FE·BE Patch Image를 발행하고 Production CD로 재배포한다.
6. 외부에 공유된 적이 있는 정적 AWS 자격 증명의 폐기·재발급을 확인한다.

### 상세 작업 기록

- `docs/2026-09-23-v1-production-deployment-and-troubleshooting-worklog.md`

## 2026-09-25 Semantic Versioning 자동화 검토

### 결정

- Semantic Versioning 자동화는 현재 V1 장애 해결 범위에서 제외한다.
- 현재의 수동 `VERSION` 관리와 기존 Tag 중복 방어를 유지한다.
- 추후 자동화 도입 시 Release Please 기반 Release PR 방식을 우선 검토한다.
- Docker Image 발행은 자동화하더라도 Production CD는 수동 승인 방식을 유지하는 것을 기본안으로 한다.

### 도입 전 우선 작업

1. S3 상품 이미지와 DB Object Key 매핑 문제를 해결한다.
2. FE·BE 상품 정렬 Enum 불일치를 해결한다.
3. Production 기능과 Rollback을 추가 검증한다.
4. Conventional Commit과 Squash Merge 규칙을 팀에서 합의한다.

### Backlog 문서

- `docs/backlog/2026-09-25-semantic-versioning-automation-proposal.md`

## 2026-09-25 부하 테스트 2단계 준비 업데이트

### 완료한 작업

- k6 기반의 읽기 전용 상품 목록 부하 테스트를 추가했다.
- `smoke`, `baseline`, `load` 프로파일을 분리했다.
- 운영 도메인의 baseline/load 실행에는 `ALLOW_PRODUCTION=true`가 필요하도록 안전장치를 추가했다.
- HTTP 오류, 응답 구조, p95/p99, business error와 dropped iteration 기준을 정의했다.
- 운영 실행 전 점검, 중단 조건, 결과 기록 항목을 문서화했다.

### 검증한 항목

- 실제 Backend 응답 계약에 `data.products`가 존재함을 코드에서 확인했다.
- 운영 Nginx가 `/api/products`를 Backend `/products`로 전달함을 확인했다.
- 상품 목록의 `POPULAR` 정렬 값이 Backend에서 지원됨을 확인했다.

### 남은 문제

- 현재 로컬 환경에 k6가 설치되어 있지 않고 Docker daemon도 응답하지 않아 실제 k6 실행 검증은 아직 하지 못했다.
- 임시 latency SLO는 제품 요구사항과 첫 측정 결과를 바탕으로 확정해야 한다.
- JVM, HikariCP와 MySQL 상세 메트릭을 동일 시간축으로 수집하는 구성이 필요하다.
- 인증 API 부하 테스트용 다중 계정과 토큰 데이터가 아직 준비되지 않았다.

### 다음 작업

1. 운영 모니터링을 연 상태에서 3회 요청 smoke 프로파일을 실행한다.
2. Smoke 통과 후 5 RPS baseline을 실행하고 서버 지표와 함께 결과를 기록한다.
3. 서버가 회복된 것을 확인한 뒤 최대 30 RPS load 프로파일을 실행한다.
4. 측정 결과를 바탕으로 SLO와 중단 조건을 조정한다.
5. 상품 상세와 선물 이력의 인증 시나리오를 추가한다.

## 2026-09-25 상품 목록 부하 테스트 결과 문서화

### 완료한 작업

- Smoke, 5 RPS Baseline, 1차 Load 중단 실행, 2차 Load 완료 실행의 k6 원본 지표를 Markdown 표로 기록했다.
- 각 테스트의 종류, 테스트 범위, 결과 판정과 k6 지표의 의미를 문서화했다.
- Baseline과 2차 Load의 latency 변화를 비교하고 실행시간 불일치에 따른 해석 제한을 기록했다.

### 결과 문서

- `load-tests/results/2026-09-25-products-list-stage2.md`

### 남은 문제

- 2차 Load의 scenario 시간 32분과 wall-clock 41분 40초 차이 원인을 확인해야 한다.
- EC2, JVM, HikariCP와 MySQL 자원 지표가 없어 서버 여유 용량은 아직 판정할 수 없다.
- 30 RPS 고정 구간만의 percentile을 분리해서 수집해야 한다.

## 2026-09-26 FE·BE 이미지 버전 관리 가이드 문서화

### 완료한 작업

- 풀스택 팀원이 공통으로 사용할 수동 Semantic Versioning 절차를 문서화했다.
- FE·BE의 `VERSION` 관리, `main` Push 이미지 발행, Production CD 입력의 관계를 정리했다.
- PATCH·MINOR·MAJOR 판단 기준과 릴리스 체크리스트를 추가했다.
- Docker 이미지 버전과 Flyway Migration Version의 차이를 명시했다.

### 검증한 항목

- FE·BE 모두 현재 `VERSION` 값이 `1.0.0`이다.
- FE·BE CI는 `main` Push에서만 운영 이미지를 발행한다.
- CI는 기존 Git Tag 및 Docker Image Tag의 중복 발행을 차단한다.
- FE와 BE 이미지는 각 저장소에서 독립적으로 버전 관리된다.

### 남은 문제

- 버전 증가와 Release Note 생성은 아직 자동화되지 않았다.
- 배포 전 버전 결정 및 `VERSION` 변경을 팀 절차로 정착시켜야 한다.

### 다음 작업

1. 가이드를 팀에 공유하고 릴리스 담당 시점을 합의한다.
2. 다음 FE·BE 수정 배포에서 수동 버전 관리 절차를 검증한다.
3. 운영 안정화 후 Release Please 기반 자동화를 별도 작업으로 진행한다.

### 가이드 문서

- `docs/guides/manual-semantic-versioning-guide.md`
