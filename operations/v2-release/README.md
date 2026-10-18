# V2 릴리스 실행 안내

## 소유권
- FE 레포: main CI 검증 → 버전별 정적 파일 S3 보관 → FE 레포의 Staging 배포 → 운영 Environment 승인 후 동일 파일 승격.
- Cloud 레포: BE 버전 선택 → ECR Digest 고정 → ECS 배포. FE 입력·Docker 추출·S3/CloudFront 배포 권한 없음.
- AI 배포는 미구현이며 이번 변경에 포함하지 않음.
- Develop은 기존 Docker/ECR/SSM 자동 배포 유지.
- main PR·릴리스는 풀스택 팀 결정. 이 코드는 빈 Service/DB 생성이나 운영 트래픽 전환 도구가 아님.

## 권한 준비
기본 Terraform은 BE ECR publisher와 FE S3 publisher Role·정책만 생성(예상 4 add).
FE ECR 생성 없음. 기존 공유 비공개 S3와 BE ECR은 유지.
**이전 release-delivery.tfplan은 사용 금지. 반드시 변경된 코드로 새 저장 Plan을 검토·승인.**

- BE Repository Variable: RELEASE_ECR_ROLE_ARN.
- FE Repository Variable: RELEASE_S3_ROLE_ARN.
- Cloud의 deployment_targets: 환경별 실제 Task/Execution Role과 확인한 Cloud OIDC sub. S3 권한 없음.
- frontend_deployment_targets: 실제 FE 환경 Distribution ID. FE 레포의 환경 OIDC만 신뢰.
- 기본 deployment_targets 두 종류는 빈 map이며 배포 Role은 자동 생성하지 않음.
- OIDC는 확인한 owner/repo ID 포함 sub 사용. FE/BE main 발행과 환경 배포 Identity 구분.
- Secret 값·장기 Access Key 발급 없음. 이미지·정적 파일 저장 및 전송·무효화 비용 별도.

## GitHub 설정
Cloud Environment: v2-staging / v2-production. main만 허용하고 production Required reviewers 필수.
변수: V2_DEPLOY_ROLE_ARN, V2_CD_READY, V2_TASK_DEFINITION_OWNER.
Task 소유권 이전 전 V2_TASK_DEFINITION_OWNER=cd 설정 금지.

FE Environment: fe-v2-staging / fe-v2-production. main만 허용하고 production Required reviewers 필수.
변수: FE_DEPLOY_ROLE_ARN, FE_CD_READY, FE_CLOUDFRONT_DISTRIBUTION_ID.
FE workflow·산출물·Role이 준비되기 전 FE_CD_READY=true 설정 금지.
FE 운영은 Staging 배포 API 검증 후 승인 대기. 이는 기능 QA 합격을 의미하지 않으며 승인자가 별도 확인.
FE main 성공 시 FE 레포 Delivery 시작. 수동 버전 재배포·복귀도 FE 레포에서 진행.
required_reviewers 조회 권한·환경 보호 정책은 실제 실행 전 검증 필요.

## BE 전제
- Main/Payment 각각 Service·Task Definition, 같은 Cluster/EC2 공유 가능.
- 최소2 Task, 단일 완료된 배포, 100/200 Rolling, Circuit Breaker enable+rollback 필요.
- 같은 Service Connect namespace, Payment alias payment-server:8090.
- Main PAYMENT_SERVER_BASE_URL=http://payment-server:8090.
- split-v2는 별도 DB migration 완료 및 SPRING_FLYWAY_ENABLED=false 명시 필요.
- Payment → Main 순서. 실패 시 이전 Task Definition으로 역순 복구. DB 복구는 아님.
- Terraform의 task_definition 관리와 CD가 경쟁하지 않도록 먼저 소유권 정렬.
- legacy-v1: 기존 backend Service에 v1-<VERSION>-amd64 태그의 검증된 V1 이미지 사용.
- 운영 BE1.3.0의 기존 mirror는 재빌드 불필요. FE 기존 정적 파일도 이번 변경만으로 덮어쓰지 않음.
- 실제 Service/ALB/DB·용량·정합성·인증/결제 기능 QA는 별도 확인.

## FE 계약
- FE 이미지 ECR 발행 제거. S3 releases/frontend/<VERSION>/files/와 manifest.json에 소스 SHA·파일 SHA256 기록.
- 버전은 VERSION 파일 기준. 다른 소스·내용으로 같은 버전 발행 금지. 조건부 쓰기, manifest 마지막.
- 서비스 경로 frontend/staging/ 또는 frontend/production/만 해당 환경 Role이 수정.
- release 파일을 다운로드해 SHA256 검사 후 동일 파일 배포. Docker 실행·재빌드 없음.
- 버킷 공개 차단·버전 관리·환경별 OAC OriginPath와 읽기 정책 확인.
- assets 먼저/index 마지막, 기존 파일 삭제 없음. 기존 이름 다른 내용 자산은 중단(해시 파일명 필요).
- 실패 시 이전 index 조건부 복구·CloudFront 무효화. 최초 배포/강제 취소는 수동 복구 가능성 있음.
- Production /api behavior·실제 API rewrite·인증·쿠키·캐시 비활성 설정 필요.
- Staging viewer 직접403은 VPN 제한이면 정상일 수 있음. 제한을 풀어 검사를 우회하지 않음.
- 별도 환경 재빌드가 없어 Vite 설정은 두 환경에서 공통 사용 가능한 /api 상대주소 계약 필요.
- 서비스 역할에는 릴리스 보관 경로 PutObject·삭제 권한 없음.
- S3 코드의 불변 검사는 IAM 관리자에 대한 WORM 보장은 아님. 운영 권한 변경은 별도 검토.

## 검증·복구
Cloud: python3 -m unittest discover -s scripts -p test_v2_release_delivery.py
FE: python3 -m unittest discover -s .github/scripts -p test_frontend_static_release.py
Terraform: mock Provider 검사만 실제 AWS 없는 로컬 실행 가능.
실제 OIDC/S3/ECR/ECS/CloudFront는 사용자 직접 검증·승인 후 실행.
V1 cd-production.yml 복구 경로 보존. 새 V2 split 이미지를 V1 단일컨테이너로 자동 배포하지 않음.
