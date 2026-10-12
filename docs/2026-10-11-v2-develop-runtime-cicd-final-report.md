# V2 Develop Runtime·CI/CD 최종 작업 보고서

- 작업일: 2026-10-10~2026-10-11 KST
- 범위: V1 Benchmark 보존, V2 Develop Runtime, Tailscale 접근, FE·BE ECR CI와 자동 배포 CD
- 최종 상태: Runtime·Tailnet 접근 구축 완료, 이전 정상 Candidate 서비스 안정화, 부팅 재조정 반복 결함 후속 수정 필요
- 공개 문서 제외 대상: AWS Account ID, Instance ID, IAM Role ARN, Public IP, Tailnet Domain, Secret 원문

## 1. 최종 결과

| 목표 | 결과 |
|---|---|
| 기존 V1 비교 환경 보존 | `v1-benchmark.seonjalal.com`으로 분리, HTTPS와 Browser 검증 완료 |
| 신규 Develop 서버 분리 | V2 Foundation VPC의 전용 EC2 생성 완료 |
| 개발자 전용 접근 | Public Ingress 0개, SSM 관리와 Tailscale Serve HTTPS 적용 |
| FE·BE Image Registry | ECR Private Repository, Immutable SHA Tag와 Push Scan 적용 |
| Secret 관리 | Secrets Manager를 Source of Truth로 적용, 배포 시 임시 파일 생성 후 제거 |
| Develop CI | FE Image 1개, BE Main·Payment Image 2개 자동 발행 |
| Develop CD | 각 Repository의 `develop` Merge 성공 후 Develop EC2 자동 배포 |
| 실패 대응 | Health 실패 시 이전 `.env`와 Container 조합 복구 |
| 서버 중지 대응 | Parameter Store 희망 SHA 보존 확인, 실제 다음 부팅 재조정에서 실패·Rollback 반복 결함 발견 |
| 최종 검증 | 이전 정상 Candidate의 네 Container Health·Tailnet HTTPS·Browser 주요 기능 확인 |

## 2. 최종 구조

```text
FE develop Merge                    BE develop Merge
  │                                  │
  ├─ Test·Build                      ├─ Test·Build
  ├─ FE Image → ECR                  ├─ Main·Payment Image → ECR
  └─ FE 희망 SHA → Parameter Store   └─ BE 희망 SHA → Parameter Store
                    │
                    ▼
          Develop EC2 실행 상태 확인
             ├─ Stopped → 희망 SHA 보존 후 성공 종료
             └─ Running → 전용 SSM Document 실행
                              │
                              ▼
                    reconcile.sh + Host flock
                              │
                    FE·BE 희망 SHA 동시 조회
                              │
                    Image URI 세 개 원자적 교체
                              │
                    Compose Pull·Up + Health
                       ├─ 성공 → 신규 조합 유지
                       └─ 실패 → 이전 조합 복구

Mac의 승인된 Tailscale 장치
  → MagicDNS·Serve HTTPS
  → 127.0.0.1:8080 Frontend
  → /api Reverse Proxy
  → Main Backend
```

Production은 위 흐름과 분리해 Cloud Repository의 명시적 승인 기반 Continuous Delivery 유지.

## 3. 작업 순서와 선택 근거

### 3.1 V1과 신규 Develop 분리

- 기존 개발 겸 Staging 서버를 V1 Benchmark로 보존.
- V1과 V2의 동일 Image·Fixture 비교 기준 유지 목적.
- 지속적인 `develop` Merge가 Benchmark 기준을 변경하지 않도록 신규 Develop EC2 별도 생성.
- 기존 `dev.seonjalal.com` Public A Record 제거와 V1 전용 이름으로 이전.

### 3.2 신규 Develop 접근 경계

- Public Subnet과 임시 Public IPv4 사용, Security Group Public Ingress 0개 유지.
- 서버 관리에 Public SSH 대신 SSM Session Manager 사용.
- 개발자 Web 접근에 Route 53 Public Domain 대신 Tailscale Device Identity와 MagicDNS 사용.
- Stop·Start로 Public IPv4가 바뀌어도 사용자 접속 주소 변경이 필요 없는 구조.

### 3.3 Runtime 구성

- Amazon Linux 2023 공식 AMI 기반 재생성 가능 구조.
- Docker Compose로 Frontend, Main Backend, Payment Server, MySQL 실행.
- Frontend만 Host Loopback `127.0.0.1:8080`에 Bind.
- Backend와 Database는 Compose Network 내부에만 노출.
- Tailscale Serve가 Loopback Frontend를 Tailnet HTTPS로 제공.

### 3.4 ECR과 CI

- FE·BE·AI용 ECR Private Repository 생성.
- 기존 Tag 변경 방지를 위한 `IMMUTABLE`, Push 시 Scan, Lifecycle Policy 적용.
- GitHub 장기 Access Key 대신 Repository·Branch가 제한된 OIDC Role 적용.
- FE `develop` Merge는 FE Image 1개, BE Merge는 Main Backend와 Payment Server Image 2개 발행.
- Commit Full SHA 기반 Tag로 실행 코드와 Image의 추적 가능성 확보.

### 3.5 Secret 경계

- 비민감 Runtime 설정만 권한 `0600`의 `/opt/seonjalal-develop/.env`에 보관.
- DB Password·JWT·HMAC·Profiling Token은 Secrets Manager에 보관.
- 배포 시 Secret을 `/run`의 임시 env 파일로 생성해 Compose에 주입한 뒤 즉시 제거.
- ECR Login Token도 Image Pull 이후 Docker 설정에서 제거.

### 3.6 자동 배포 책임 분리

- Application Repository가 Merge Event, 검증, Image 발행과 자기 Component 희망 SHA 갱신 담당.
- Cloud Repository가 IAM, Parameter Store, SSM Document, 서버 Script, Health와 Rollback 담당.
- Trigger는 Application Repository에 두고 배포 구현 계약은 Cloud Repository에 두는 구조.
- Production은 Develop과 별도 Role·Target·Workflow로 분리하고 수동 승인 유지.

## 4. 주요 코드 변화

| 파일 | 변화 | 의미 |
|---|---|---|
| `infrastructure/terraform/environments/develop/runtime/release.tf` | FE·BE 희망 SHA Parameter 추가 | 서버가 꺼져도 최신 배포 목표 보존 |
| `infrastructure/terraform/environments/develop/runtime/github_oidc.tf` | FE·BE Deploy Role과 최소 권한 추가 | 자기 Parameter와 지정 EC2만 변경 가능 |
| `infrastructure/terraform/environments/develop/runtime/ssm.tf` | 입력 Parameter 없는 전용 재조정 Document 추가 | GitHub에서 임의 Shell 실행 방지 |
| `operations/develop/runtime/scripts/reconcile.sh` | Lock·SHA 조회·원자적 Image 교체·Health·Rollback 추가 | 동시 Merge 충돌과 실패 배포 보호 |
| `operations/develop/runtime/systemd/seonjalal-develop-reconcile.service` | Boot oneshot 추가 | Offline Merge의 다음 부팅 반영 |
| FE `.github/workflows/ci.yml` | `publish-develop` 이후 `deploy-develop` 추가 | FE Merge 직후 자동 배포 |
| BE `.github/workflows/ci.yml` | `publish-develop` 이후 `deploy-develop` 추가 | Main·Payment Merge 직후 자동 배포 |

## 5. 주요 명령의 의미

### `terraform init -reconfigure -backend-config=backend.hcl`

- Runtime Terraform이 Local State가 아니라 지정한 S3 Backend State를 사용하도록 초기화.
- S3 State Object가 현재 인프라의 공동 기준이며 Versioning은 잘못된 State 변경 복구 수단.
- `backend.hcl`과 `terraform.tfvars`는 Local 설정 파일로 유지하고 Secret 원문은 State 입력에서 제외.

### `terraform plan -out=<plan>`과 `terraform apply <plan>`

- Plan에서 Resource 추가·변경·삭제 범위를 사전 고정.
- 승인한 저장 Plan만 Apply해 검토 이후 구성 재계산 방지.
- Runtime, ECR, OIDC, Secret, Product Image 권한과 CD Resource를 단계별 분리 적용.

### `aws ssm start-session`

- Public SSH 22와 Key Pair 없이 IAM 인증을 거쳐 EC2 Shell 접속.
- Session Manager Plugin이 Mac에 필요하며 EC2의 SSM Agent와 Instance Role이 Online 상태여야 함.

### `aws ssm send-command`

- 파일 설치·배포·검증을 Public SSH 없이 수행.
- 일반 운영 배포에서는 범용 Shell 대신 고정 명령의 전용 SSM Document 사용.
- Command ID로 실행 상태와 Response Code를 확인하고 Secret 원문은 출력하지 않도록 제한.

### `tailscale ping`과 Tailnet HTTPS Curl

- 장치 이름을 Tailscale Identity와 MagicDNS로 해석하고 암호화된 Tailnet 경로 확인.
- 직접 P2P가 성립하지 않으면 DERP Relay 사용 가능.
- 이번 검증은 DERP Tokyo 경로였지만 Frontend HTTPS 200과 Backend Health `UP` 확인.

### GitHub Actions `publish-develop`과 `deploy-develop`

- `publish-develop`: 검증 성공 후 Immutable ECR Image 발행.
- `deploy-develop`: OIDC 임시 AWS Credential 획득, 자기 희망 SHA 갱신, EC2 Online이면 재조정 실행.
- Production `publish` Job은 `develop` Merge에서 Skip되어 환경 간 배포 경계 유지.

## 6. 문제와 트러블슈팅

| 현상 | 확인된 원인 | 해결 | 검증 |
|---|---|---|---|
| AWS Session 만료 | `aws login` 세션 만료 | `seonjalal-production` Profile 재인증 | 예상 IAM User ARN 확인 |
| SSM 접속 실패 | Mac에 Session Manager Plugin 없음 | AWS 서명·Notarization 확인 후 Plugin 설치 | Version 확인과 실제 Session 접속 |
| `docker compose` 미인식 | Amazon Linux Repository에 Compose Plugin Package 없음 | 공식 Release Binary와 SHA-256 검증 후 CLI Plugin 설치 | Compose Version·Plugin Path 확인 |
| Container에서 Instance Role 사용 실패 가능성 | IMDS Hop Limit이 Container 경로에 부족 | EC2 Metadata Hop Limit 조정 | 배포와 ECR·S3 접근 성공 |
| macOS Tar 확장 Header Warning | Apple Extended Attribute 포함 | `COPYFILE_DISABLE=1`, `tar --no-xattrs` 사용 | Hash 일치와 원격 설치 성공 |
| V1 HTTPS Timeout | 현재 Mac `/32`가 SG에 없음 | HTTPS 443 `/32` 임시 허용 | Nginx까지 요청 도달 |
| V1 HTTPS 403 | Host Nginx Allowlist에 현재 `/32` 없음 | Backup 후 Allowlist 추가, `nginx -t`, Reload | HTTP 200 확인 |
| DNS Challenge 값 비교 실패 | Clipboard에 Certbot 안내문까지 포함 | Challenge 한 줄만 보이지 않게 입력하고 43자 계약 검사 | TXT 응답과 정확히 일치 |
| 최초 재조정 실패 | `ec2-user`가 `/run/lock`에 Lock 생성 불가 | Lock을 `/opt/seonjalal-develop/.deploy.lock`으로 이동 | 재조정·Health·Rollback 경로 성공 |
| Tailscale 직접 연결 미수립 | 현재 NAT·UDP 경로에서 Peer 직접 연결 실패 | DERP Relay 사용 유지 | HTTPS와 Backend Health 정상 |
| 팀원 MagicDNS 이름 해석 실패 | 팀원 장치가 Tailnet에 로그인했지만 `Needs approval` 상태 | 예상된 팀원 장치 승인과 HTTPS 443 전용 Grant 유지 | 승인 전 DNS 차단과 관리자 장치 접근 분리 확인 |
| EC2 Start 직후 HTTP 502 | 부팅 재조정 중 Frontend가 아직 생성되지 않아 Serve Upstream 부재 | 재조정 진행 상태와 Loopback 응답 확인 | Backend 이후 Frontend 시작과 일시 HTTP 200 확인 |
| 정상 화면과 502 반복 | 최신 Candidate의 Compose Health 실패 후 Rollback, 실패 코드에 대한 systemd 자동 재시도 반복 | 현재 부팅 전용 `Restart=no` Drop-in 적용과 마지막 Rollback 완료 대기 | `NRestarts=10` 고정, 이전 Candidate 네 Container Healthy와 주요 기능 정상 |
| runtime mask 미적용 | `/etc/systemd/system`의 실제 Unit이 유지되고 `is-enabled`가 `enabled` 반환 | `/run/systemd/system/...service.d`의 runtime Drop-in으로 Restart 속성 변경 | `Restart=no` 확인 |

## 7. 최종 검증 증거

- FE Workflow Run `38135320513`: `validate`, `publish-develop`, `deploy-develop` 성공.
- BE Workflow Run `38135337941`: `validate`, `publish-develop`, `deploy-develop` 성공.
- Production `publish` Job: 두 Run 모두 의도된 Skip.
- GitHub Merge SHA와 Parameter Store 희망 SHA 일치.
- FE·Main Backend·Payment Server Container Image Tag와 각 Merge SHA 일치.
- Frontend·Main Backend·Payment Server·MySQL Container 모두 Healthy.
- Frontend Loopback, Backend Proxy Health, Tailnet HTTPS 성공.
- Runtime 임시 Secret 파일과 ECR Login Credential 제거 확인.
- 승인된 Mac의 Tailscale Browser 화면 확인.

### 2026-10-12 EC2 Stop·Start 후 추가 검증

- Develop EC2 Start와 Tailscale Machine 재연결 확인.
- HTTPS 443 전용 Tailnet Grant와 관리자 Browser 접근 확인.
- 최신 희망 Candidate의 `docker compose up --wait` 실패 확인.
- 이전 정상 Candidate Rollback과 네 Container Health 확인.
- Frontend Loopback, Backend Proxy Health와 관리자 주요 기능 정상 확인.
- Rollback 이후 `Restart=on-failure`에 의한 반복 배포 10회 확인.
- 현재 부팅 전용 `Restart=no` 적용과 반복 중단 확인.
- 최신 Candidate와 Parameter Store 희망 SHA의 End-to-End 적용은 미완료 상태로 정정.

## 8. 남은 작업

| 우선순위 | 작업 | 현재 영향 |
|---:|---|---|
| 1 | 부팅 재조정 실패 로그 보존과 Rollback 후 반복 재시도 방지 | 재부팅 시 정상 화면과 502 반복 재발 가능 |
| 2 | 최신 희망 Candidate의 실제 실패 Container·원인 규명 | 현재 이전 정상 Candidate 사용 중 |
| 3 | 팀원 전원 장치 승인과 MagicDNS HTTPS End-to-End 확인 | 관리자 장치 기능 확인만 완료 |
| 4 | 현재 부팅 전용 `Restart=no`를 영구 코드 수정으로 대체 | `/run` 설정은 재부팅 시 소멸 |
| 5 | GitHub Actions Node.js·Runner·`setup-java` 경고 정리 | 현재 Workflow 성공, 향후 호환성 위험 |
| 6 | `main` Production Image의 Docker Hub → ECR 이전 | Develop은 ECR 완료, Production은 기존 경로 유지 |
| 7 | AI Dockerfile·CI·Runtime 연결 | AI 기능 미포함 |
| 8 | Docker Compose 설치 Bootstrap 코드화 | EC2 재생성 자동화 완성도 영향 |
| 9 | V1 Benchmark DNS 갱신·인증서 갱신 Runbook | V1 시험 운영 편의성 영향 |
| 10 | PLG 관측과 부하 시험 | V2 성능 비교 증거 수집 필요 |

상세 장애 기록: `docs/2026-10-12-v2-develop-boot-reconcile-rollback-loop.md`

## 9. 공식 근거

- [Terraform State](https://developer.hashicorp.com/terraform/language/state)
- [Terraform S3 Backend](https://developer.hashicorp.com/terraform/language/backend/s3)
- [GitHub OIDC와 AWS](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-aws)
- [GitHub Actions Concurrency](https://docs.github.com/en/actions/concepts/workflows-and-actions/concurrency)
- [AWS Systems Manager Run Command](https://docs.aws.amazon.com/systems-manager/latest/userguide/run-command.html)
- [AWS Systems Manager Parameter Store](https://docs.aws.amazon.com/systems-manager/latest/userguide/systems-manager-parameter-store.html)
- [AWS Secrets Manager](https://docs.aws.amazon.com/secretsmanager/latest/userguide/intro.html)
- [Tailscale Serve](https://tailscale.com/docs/features/tailscale-serve)
- [Tailscale HTTPS](https://tailscale.com/docs/how-to/set-up-https-certificates)
