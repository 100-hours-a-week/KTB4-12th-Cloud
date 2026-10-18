# V2 릴리스 CI/CD 실행 계약

현재 코드는 **준비된 인프라의 버전 교체**용이다. 빈 ECS Cluster에 Service/ALB/DB를 생성하거나 운영 트래픽을 이전하지 않는다. 실제 실행은 사용자 승인·직접 실행 절차를 따른다.

## 배포 정책

| 환경 | 시작 조건 | 실제 배포 |
|---|---|---|
| Develop | 앱 develop 머지 | 기존 ECR/SHA/SSM 자동 배포 유지 |
| Staging | Cloud V2 Release Delivery에서 버전 선택 | 수동 실행, 시험 Window와 조율 |
| Production | Cloud V2 Release Delivery에서 버전 선택 | `v2-production` Environment의 Required reviewers 승인 후 실행 |

Continuous Delivery는 배포 가능한 산출물과 절차를 자동화하면서 운영 승인도 유지할 수 있다. 모든 검증을 통과한 변경을 승인 없이 운영에 내보내는 Continuous Deployment로 자동 전환하는 작업이 아니다.

## 산출물과 태그

- BE main: `seonjalal/production/backend:main-backend-<VERSION>` 및 `payment-server-<VERSION>`
- FE main: `seonjalal/production/frontend:<VERSION>`
- VERSION은 `2.0.0` 또는 `2.0.0-rc.1` 형식. ECR/Docker 태그에서 사용할 수 없는 `+metadata` 제외
- CI는 테스트 통과 후 main 전용 OIDC Role로 발행. Immutable Repository만 허용, 동일 버전/동일 커밋의 부분 성공은 재시도 가능, 다른 커밋 태그 덮어쓰기 금지
- CD는 버전을 ECR에서 Digest로 확정하고 그 이미지만 사용. FE는 이미지를 실행하지 않고 `/usr/share/nginx/html`만 추출해 S3에 업로드, 환경별 재빌드 없음
- main 워크플로의 버전 파일 갱신 필요. Git 태그가 다른 커밋에 이미 있으면 CI 중단. 기존 DockerHub V1 버전은 보존하나 main 신규 발행은 DockerHub로 하지 않음

## 실행 전 준비

### 1. 릴리스 발행 IAM/ECR

`infrastructure/terraform/environments/shared/release-delivery`는 기존 backend ECR을 조회하고 frontend ECR/앱 main 전용 publisher Role2개를 준비한다. 기본 설정은 CD Role을 생성하지 않는다. 신규 ECR 보관·스캔 비용 영향 검토 후 사용자 Plan/승인/Apply 필요.

- 앱 저장소 각각 Repository Variable `RELEASE_ECR_ROLE_ARN` 설정: 해당 앱 publisher ARN
- 기존 Develop Role·배포 Role과 분리, Access Key 또는 Secret 값 불필요
- 현재 앱 OIDC의 repo/owner ID 포함 subject 형식 유지. 실제 GitHub OIDC customization과 일치하는지 적용 전 확인
- Cloud CD Role은 `deployment_targets`에 실제 Distribution/Task·Execution Role/정확한 OIDC subject를 지정한 환경에만 생성
- Cloud 저장소 기본/맞춤 OIDC subject 여부는 별도 조회 후 설정. 추정으로 기본·맞춤 형식을 동시에 신뢰하지 않음
- Task Execution Role도 해당 릴리스 ECR을 읽을 수 있어야 함. CD Role의 ECR 읽기 권한이 Task에 전달되지는 않음
- ECS 등록·Service 갱신·PassRole은 해당 환경만 허용. `DescribeTaskDefinition`은 AWS의 리소스 수준 권한 미지원으로 조회만 `Resource:*` 필요하므로 Task 정의의 민감값은 Secrets 참조로 관리. 이 Role은 개인 팀원에게 부여하지 않음

### 2. GitHub Environments

Cloud에 `v2-staging`, `v2-production` 생성. **Deployment branches를 main으로 제한**하고 production Required reviewers 지정. YAML `environment` 선언만으로 Required reviewers가 생성되지는 않는다. Production은 보호 규칙이 없으면 코드에서도 중단한다. 관리자 우회 방지도 별도 설정 권장.

각 Environment Variable:

| 이름 | 의미 |
|---|---|
| `V2_DEPLOY_ROLE_ARN` | 해당 환경 `seonjalal-v2-<env>-release-deployer` Role |
| `V2_FRONTEND_BUCKET` | 기존 공유 비공개 frontend 버킷 |
| `V2_CLOUDFRONT_DISTRIBUTION_ID` | 해당 환경 Distribution. Staging/Production 혼용 금지 |
| `V2_TASK_DEFINITION_OWNER` | BE의 Terraform/CD 소유권 이전 검토 후에만 `cd` |
| `V2_CD_READY` | 아래 인프라·권한·API·DB 조건을 실제 검증한 후에만 `true` |

첫 실행은 `dry_run=true` 유지. 이 모드도 ECR Pull·중지 상태의 추출용 Container 생성/삭제 같은 Runner 로컬 작업은 수행하나 AWS 쓰기는 차단한다. Production의 검증 실행도 Environment 승인을 따른다.

### 3. ECS 준비 및 소유권

- Main/Payment 각각 별도 Task Definition family + ECS Service. 같은 클러스터·EC2 Capacity 공유 가능, Task를 단 하나만 실행한다는 뜻이 아님
- Service별 최소2개/현재 정상 단일 COMPLETED 배포/100·200 Rolling/Circuit Breaker rollback 필요. 실제 부하·용량 보장값이 아니며 신규 Task를 위한 CPU/Memory/ENI 여유 검증 필요
- 이름: `seonjalal-v2-<env>-main-backend`, `...-payment-server`. Task 컨테이너 이름도 각 suffix와 일치. Main은 외부 ALB 대상, Payment는 내부 Service Connect 전용
- 두 Service의 Service Connect namespace 동일, Payment alias `payment-server:8090`, Main `PAYMENT_SERVER_BASE_URL=http://payment-server:8090`
- 앱 간 통신 API·인증키·스키마가 신구 버전 공존 중에도 호환돼야 함. 같은 버전 태그를 쓴다고 원자적으로 동시에 바뀌지는 않음
- 분리형 앱은 **사전 DB Migration 완료 및 `SPRING_FLYWAY_ENABLED=false`를 명시한 Task**가 필요. 현재 Main의 DDL 소유를 그대로 두고 Payment를 먼저 시작하면 JPA validate 기동 경쟁 발생 가능. 별도 Migration Task/절차는 이번 CD가 자동 생성·실행하지 않음
- 배포 순서 Payment→Main. 실패 시 이전 Task Definition으로 역순 복구. ECS waiter 성공 후에도 요청한 Revision인지 확인, 자동 rollback으로 old Revision이 정상화된 것을 신규 성공으로 처리하지 않음
- DB 스키마·업무 데이터는 앱 rollback으로 복원되지 않음. expand/contract 호환성과 별도 백업·forward fix 판단 필요
- **현재 Staging rehearsal은 Terraform이 task_definition을 소유**. 해당 Root를 그대로 두고 V2 CD를 켜지 말 것. 소유권 이전 시 `aws_ecs_service`의 `task_definition`을 CD 소유로 분리(예: 해당 속성 ignore_changes)하고 Infra 설정 변경을 Task에 어떻게 반영할지 함께 확정. 새 Task 정의가 Terraform에서 생성돼도 Service에는 자동 반영되지 않는 부작용도 검토
- 현재 root의 리허설/실패 주입 실험을 이번 작업에서 수정하지 않음. 소유권 정렬 전 `V2_TASK_DEFINITION_OWNER=cd` 설정 금지

### 4. V1 이미지 이전 시험

- `backend_layout=legacy-v1`은 기존 `seonjalal-v2-<env>-backend`/컨테이너 `backend` 대상으로 동작
- ECR 태그 `v1-<VERSION>-amd64` 필요. 이미 검증한 BE1.3.0 mirror를 재빌드할 필요 없음
- FE 기존1.3.2를 이 CD로 사용하려면 **동일 이미지**를 frontend 릴리스 ECR의 `1.3.2`로 별도 복사·동일성 검증 필요. 현재 S3에 파일이 있다는 것과 ECR에 버전 이미지가 있다는 것은 다름
- 기존 단일 BE 시험을 Main/Payment 업그레이드와 혼합하지 않음. legacy 모드도 Terraform/CD 소유권·DB/API 호환성 승인이 필요

### 5. S3·CloudFront·API

- 공유 비공개 S3의 `frontend/<env>/`만 수정. 공개 차단4개·버전관리·환경별 OAC OriginPath·Distribution 한정 읽기 정책 검사
- 정적 파일 먼저, index 마지막. 기존 자산 삭제 없음. 같은 이름의 내용이 달라지면 중단해 해시/버전 파일명 사용 강제
- 신규 자산 If-None-Match, index If-Match 조건부 쓰기·S3 재다운로드 해시 확인, CloudFront 무효화 완료 대기
- 실패하면 기존 index 복원 및 무효화, 자산은 보존. 최초 배포는 이전 index가 없어 자동 복귀 불가. 업로드되지 않은 다른 버전이 끼어들면 임의 덮어쓰지 않고 중단
- FE는 `/api` 상대주소 사용. **운영 CloudFront는 `/api/*`를 Main ALB 원본으로 전달**하고 캐시 비활성/인증정보 전달/필요한 URI rewrite가 준비돼야 함. 코드의 API behavior 유무 검사만으로 rewrite·로그인·쿠키 계약 검증이 끝난 것은 아님
- Staging은 기존 VPN 중계가 `/api`를 내부 ALB로 처리. GitHub Runner에서 CloudFront viewer 직접403은 정상일 수 있음. viewer IP 제한을 풀거나 운영/타환경을 프록시로 우회하지 않음
- CI/CD 자동 검증은 ECS 상태/S3 해시/CloudFront 배포 API 수준. 별도 실제 브라우저·인증·API 읽기·결제 QA 필요

## Workflow 사용

[V2 CD](../../.github/workflows/cd-v2.yml)에서 환경/구성요소/버전 선택, DB/API 호환성 확인 체크, 기본 검증 모드 실행. 검증 후 승인된 창에 dry_run 해제. 코드 수정만으로 환경 보호·Role·Service·CloudFront가 생성된 것은 아님.

한 환경에는 한 Delivery만 실행, 취소 대신 완료/실패 결과 확인. GitHub 강제 취소·Runner 장애 시 복구 루틴 완료를 보장하지 않으므로 receipt와 서비스 상태를 별도 확인. backend와 frontend를 선택한 all도 분산 트랜잭션이 아니므로 짧은 신구 공존과 복구 지연은 존재.

V1 `cd-production.yml`은 이름만 Legacy로 표시하고 기존 수동 DockerHub→EC2 경로 유지. 기존버전 복구용이며 V2 전환 후 별도 승인으로 비활성화/제거. V2 main 새이미지를 V1 단일 컨테이너로 자동 배포하지 않음.

## 검증

Mac Cloud 저장소에서 AWS 쓰기 없이 로컬 검사:

```bash
# ECR 발행 계약·ECS 오판 방지·정적 업로드/복구 모의 검사
python3 -m unittest discover -s scripts -p 'test_v2_release_delivery.py'
```

실제 OIDC/ECR 발행/ECS/S3/CloudFront/GitHub Environment 테스트는 별도 사용자 설정·승인 후 수행. 준비가 안 된 환경의 `V2_CD_READY`를 true로 바꿔 검사를 우회하지 말 것.
