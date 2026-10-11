# V2 Develop Continuous Deployment 설계와 구현 기록

- 작업일: 2026-10-11 KST
- 대상: FE·BE `develop` Branch, Develop EC2 Runtime
- 현재 상태: FE·BE `develop` Merge 기반 자동 배포와 실행 Image·Tailnet Browser 최종 검증 완료
- 공개 문서 제외 대상: AWS Account ID, EC2 Instance ID, IAM Role ARN, Tailnet Domain

## 1. 결론

- FE·BE 각 Repository의 `develop` Merge 성공을 자동 배포 시작점으로 사용.
- Application Repository는 Image Build·ECR Push·자기 Component 희망 SHA 갱신 책임 보유.
- Cloud Repository는 IAM 최소 권한, Parameter Store, 서버 재조정·검증·Rollback 계약 소유.
- Develop은 승인 단계 없는 Continuous Deployment 적용.
- Production은 Cloud Repository에서 명시적 승인 후 진행하는 Continuous Delivery 유지.
- Develop EC2 중지 상태에서도 Merge 실패 처리 없이 희망 SHA 저장, 다음 부팅 시 최신 조합 자동 반영.

## 2. 책임 분리

| 위치 | 책임 | 제외 책임 |
|---|---|---|
| FE Repository | FE 검증, Immutable Image 발행, FE 희망 SHA 갱신, 재조정 요청 | BE SHA 변경, 서버 배포 구현 소유 |
| BE Repository | BE 검증, Main·Payment Image 발행, BE 희망 SHA 갱신, 재조정 요청 | FE SHA 변경, 서버 배포 구현 소유 |
| Cloud Repository | OIDC Role, SSM Parameter, Runtime Script, 동시 실행 제어, Health·Rollback | Application Image Build |
| Develop EC2 | FE·BE 최신 희망 SHA 조회, Compose 배포, 검증, 실패 시 이전 `.env` 복구 | GitHub 장기 Credential 보관 |

Application Repository에서 배포를 시작하되 서버 제어 로직은 Cloud Repository가 관리하는 구조. Trigger와
배포 구현 소유권을 분리해 Application Merge 직후 자동 배포 요구와 인프라 운영 계약의 단일 관리 동시 충족.

## 3. 배포 흐름

```text
FE 또는 BE develop Merge
  ↓
Repository CI 검증 성공
  ↓
Immutable ECR Image 발행 성공
  ↓
Repository 전용 GitHub OIDC Deploy Role 수임
  ↓
자기 Component의 Parameter Store 희망 SHA 갱신
  ├─ EC2 Offline → 성공 종료, 다음 부팅 재조정 대기
  └─ EC2 Online  → SSM Run Command로 reconcile.sh 실행
                         ↓
                 Host flock 획득
                         ↓
                 FE·BE 희망 SHA 모두 조회
                         ↓
                 .env의 Image URI 세 개 원자적 교체
                         ↓
                 Pull → Compose Up → Health 검증
                    ├─ 성공 → 새 Candidate 유지
                    └─ 실패 → 이전 .env와 Container 조합 복구
```

## 4. 주요 안전장치

| 위험 | 안전장치 |
|---|---|
| FE·BE 동시 Merge의 Compose 충돌 | Host `flock`으로 한 번에 한 재조정만 실행 |
| 검증 전 Image 배포 | `deploy-develop`이 `publish-develop` 성공에 의존 |
| 잘못된 Image Tag | 40자리 소문자 Full Git SHA 검증과 Immutable ECR Tag 사용 |
| 서버 중지 중 Merge 유실 | Parameter Store에 희망 SHA 영속 저장, 부팅 서비스에서 재조정 |
| 신규 Container Health 실패 | 이전 `.env` 복원 후 이전 Candidate 재배포 |
| GitHub 장기 AWS Key | Repository·Branch가 제한된 OIDC Role 사용 |
| 과도한 Repository 권한 | FE·BE Role이 자기 Parameter만 Get·Put 가능하도록 분리 |
| 범용 Shell 명령 실행 | 명령 입력이 없는 전용 SSM Document만 `SendCommand` 허용 |
| SSM 기본 Waiter 조기 종료 | 최대 12분간 실제 Command 상태 Polling |
| Terraform과 CI의 SHA 충돌 | 최초 Parameter 생성 후 `ignore_changes = [value]`로 운영 소유권을 CI에 이전 |

## 5. 변경 파일

### Cloud Repository

- `infrastructure/terraform/environments/develop/runtime/release.tf`
  - FE·BE 희망 SHA Parameter Store Resource 추가.
- `infrastructure/terraform/environments/develop/runtime/github_oidc.tf`
  - FE·BE Repository별 Develop Deploy OIDC Role과 최소 권한 추가.
- `infrastructure/terraform/environments/develop/runtime/ssm.tf`
  - 고정된 Runtime 재조정 명령만 실행하는 전용 SSM Command Document 추가.
- `infrastructure/terraform/environments/develop/runtime/variables.tf`
  - 최초 검증 완료 FE·BE SHA 입력 추가.
- `infrastructure/terraform/environments/develop/runtime/outputs.tf`
  - GitHub Variable 등록용 Deploy Role ARN Output 추가.
- `operations/develop/runtime/scripts/reconcile.sh`
  - 희망 SHA 조회, Lock, Image 교체, 배포 검증과 Rollback 추가.
- `operations/develop/runtime/systemd/seonjalal-develop-reconcile.service`
  - EC2 부팅 시 미반영 희망 상태 자동 재조정 추가.

### FE·BE Repository

- `.github/workflows/ci.yml`
  - `publish-develop` 성공 후 자동 실행되는 `deploy-develop` Job 추가.
  - EC2 Offline 분기, SSM 실행 결과 Polling, 실패 시 자기 희망 SHA 복구 추가.

## 6. 검증 상태

### 완료

- Terraform 파일 `fmt -check` 통과.
- `reconcile.sh`, `deploy.sh`, `verify.sh` Bash 문법 검사 통과.
- FE·BE Workflow YAML Parse 통과.
- Runtime Script 실행 권한 `0750` 확인.
- Cloud Repository 단독 수동 CD 초안 제거와 Application Repository Trigger 구조로 정정.
- Terraform Apply `7 added, 0 changed, 0 destroyed`와 Apply 직후 무변경 Plan 확인.
- 전용 SSM Document, FE·BE 희망 SHA Parameter와 Deploy OIDC Role 생성 완료.
- Develop EC2의 `reconcile.sh` 설치와 부팅 systemd Unit Enable 완료.
- 전용 SSM Document를 통한 ECR Pull, Compose 적용, 네 Container Health와 Proxy 검증 완료.
- FE·BE Repository에 `DEV_CD_ROLE_ARN`, `DEV_EC2_INSTANCE_ID` Variable 등록 완료.
- FE PR #177과 BE PR #326 Merge 후 두 Workflow의 `validate`, `publish-develop`, `deploy-develop` 성공.
- FE Workflow Run `38135320513`, BE Workflow Run `38135337941` 성공.
- Workflow Merge SHA와 Parameter Store의 FE·BE 희망 SHA 일치 확인.
- FE·Main Backend·Payment Server 실행 Image가 각 Merge SHA 기반 Immutable Tag와 일치함을 확인.
- 네 Container Healthy, Frontend Loopback과 Backend Proxy Health 성공.
- Runtime 임시 Secret 파일과 Docker ECR Login Credential 제거 확인.
- Mac의 Tailnet HTTPS Frontend·Backend 응답과 Browser 화면 확인.

### 후속 검증

- EC2 중지 중 Merge 후 부팅 재조정 시나리오의 실제 Stop·Merge·Start 통합 검증.
- GitHub Actions Node.js 20 강제 전환과 Ubuntu Runner Migration 경고 대응.
- BE `actions/setup-java@v4`를 지원되는 Version으로 갱신.
- 기존 FE React Hook·Fast Refresh Warning 별도 정리.

## 7. 트러블슈팅

| 현상 | 원인 | 수정 | 영향 |
|---|---|---|---|
| 최초 재조정에서 `/run/lock/...: Permission denied` | 전용 SSM Document가 `ec2-user`로 실행되지만 `/run/lock`은 Root 소유 | Lock 파일을 `ec2-user:docker` 소유의 `/opt/seonjalal-develop/.deploy.lock`으로 변경 | `.env` 교체 전 실패로 Container·Data 변경 없음 |
| Tailscale Ping의 `direct connection not established` | 현재 Network에서 Peer 간 직접 UDP 경로가 성립하지 않아 DERP Tokyo 중계 사용 | 별도 변경 없이 HTTPS와 Backend Health 검증, 필요 시 NAT·UDP 정책 후속 점검 | 기능 영향 없음, 직접 연결 대비 Latency 증가 가능 |

## 8. Rollback 원칙

- 배포 단계 실패: `reconcile.sh`의 이전 `.env`와 이전 Container 조합 자동 복구.
- GitHub Job 실패: 해당 Repository가 갱신한 희망 SHA를 직전 값으로 복구.
- Workflow 문제: `deploy-develop` Job 제거 또는 비활성화, 기존 수동 SSM 배포 경로 사용.
- IAM 문제: Application Workflow 중단 후 Terraform에서 Deploy Role·Policy 제거 Plan 별도 검토.
- Parameter 제거: Workflow 중단과 Runtime Unit 비활성화 이후 별도 Terraform Plan으로 수행.

## 9. 공식 근거

- [GitHub Actions Workflow](https://docs.github.com/en/actions/concepts/workflows-and-actions/workflows)
- [GitHub Actions Concurrency](https://docs.github.com/en/actions/concepts/workflows-and-actions/concurrency)
- [GitHub OIDC와 AWS](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-aws)
- [AWS Systems Manager Run Command](https://docs.aws.amazon.com/systems-manager/latest/userguide/run-command.html)
- [AWS Systems Manager Parameter Store](https://docs.aws.amazon.com/systems-manager/latest/userguide/systems-manager-parameter-store.html)
- [AWS의 Continuous Deployment 권고](https://docs.aws.amazon.com/wellarchitected/latest/devops-guidance/dl.cd.7-remove-manual-approvals-to-practice-continuous-deployment.html)
- [AWS Production 승인 단계 지침](https://docs.aws.amazon.com/prescriptive-guidance/latest/choosing-git-branch-approach/production-environment.html)
