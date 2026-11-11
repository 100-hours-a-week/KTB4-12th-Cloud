# V3 Argo CD 적용·승계 안내

## 현재 범위

- 2026-11-12 기준 Argo CD 설치·Repository 연결·7개 App 수동 승계 및 전후 보존 확인 완료. 자동 동기화 활성화·Git 변경 자동 반영 검증은 대기.
- 기존 수동 Helm 리소스를 승계한 상태. 앞으로 7개 앱의 배포 소유권은 Argo CD로 유지하고 수동 helm upgrade/rollback/uninstall과 혼용하지 않음.
- 목표: 승인된 Git의 Helm values/Digest 변경 → Argo CD 감지 → Kubernetes 반영 → Health 확인.
- ECR에 새 이미지가 생긴 것만으로는 배포하지 않음. Cloud의 해당 Digest 변경 PR 검토·머지가 릴리스 경계.
- 초기 승계는 `feat/v3-argocd`에서 수동 Sync. 기본값은 이 재현 경로로 보존. Chart·현재 운영 설정·GitOps overlay의 develop 머지 확인 후 별도 production-automation.yaml로 자동 배포 활성화.
- PR #124는 최초 fa7c0b3까지만 반영. 후속 3.0.1 Digest·Hikari 수정·AI Chart·스케줄러 및 Argo CD 코드는 이 후속 PR에 함께 포함. PR #124가 머지됐다는 사실만으로 현재 develop이 운영과 같다고 판단하지 않음.

## 생성 파일과 설정 의미

| 파일 | 역할 |
| --- | --- |
| controller-values.yaml | Argo CD 제어 도구 설치값. 공식 argo-cd Chart 10.17.2 / app v3.6.1 기준 |
| charts/production-apps | AppProject와 BE 6개·AI 1개 Application 정의 |
| production-automation.yaml | develop 기준 자동 Sync·selfHeal 활성화 overlay. 머지·값 대조 후 명시적 적용 |
| ../helm/environments/production/gitops/backend.yaml | 기존 수동 `--set replicas=1`을 Git 희망 상태로 명시 |
| ../helm/environments/production/gitops/ai.yaml | 기존 AI replicas=1·launchApproved=true 승계 |
| ../scripts/validate-v3-gitops.rb | 로컬 렌더 및 선택적 읽기 전용 기존 Helm 비교 |
| ../.github/workflows/validate-v3-gitops.yml | PR·해당 작업 브랜치·develop의 렌더 검사. AWS/CD 변경 없음 |

```yaml
# 실제 Application의 핵심 설정. 전체 정의는 charts/production-apps/templates/applications.yaml 참조.
source:
  targetRevision: feat/v3-argocd
  helm:
    releaseName: member-service
    valueFiles:
      - ../../environments/production/backend/common.yaml
      - ../../environments/production/backend/main.yaml
      - ../../environments/production/backend/member-service.yaml
      - ../../environments/production/gitops/backend.yaml
syncPolicy:
  automated:
    enabled: false
    prune: false
    selfHeal: false
    allowEmpty: false
  syncOptions:
    - FailOnSharedResource=true
    - Prune=false
```

- 기존 Helm Release와 Application 이름 동일. annotation 추적으로 app.kubernetes.io/instance selector 보존.
- 공통 파일 replicas:0을 바꾸지 않고 마지막 GitOps overlay에서 1로 덮어씀. 값 병합 순서 변경 금지.
- recommendation-service만 기존 공통 스케줄링·AI 발행/소비 true 유지. 다른 Main은 false.
- 앱 관리 허용: production namespace의 Deployment·ConfigMap·Service·PDB. Secret·PVC·StatefulSet·Job·Ingress·RBAC·CRD는 이 AppProject의 배포 허용 범위 밖.
- Redis·RabbitMQ·DB Migration·ESO·IRSA·ALB·Origin 검증 Secret은 이번 App 관리 대상에 포함하지 않음. 기존 데이터 서비스와 앱을 동시에 승계하지 않는 선택.
- Project 허용 목록은 Argo CD 정책 경계. 기본 설치의 controller/server ClusterRole이 같은 수준으로 최소화됐다는 의미는 아님. 관리자는 저장소 변경과 AppProject 변경 권한을 보호해야 함.
- Application 삭제에 따른 연쇄 리소스 삭제 finalizer 미사용, prune/allowEmpty 비활성. Force/Replace 옵션 금지.

## 자원·접속·비용

- 기존 workload=system MNG에 controller·repo-server·server·내부 캐시 Redis를 각각 1개로 배치. 이 Redis는 사용자 인증/상품 Redis와 별개의 재생성 가능한 Argo 캐시.
- 핵심 상시 4개 프로세스 requests 합계 300m CPU·768Mi 메모리. 초기값이며 최적 실측 아님. init Job 등의 일시 자원은 별도. 노드 실여유·upstream 렌더 확인 후 설치 승인 필요.
- ApplicationSet 실행 수 0, Dex·Notifications·Redis HA 미사용. 고가용성 설치 대비 비용·복잡도 감소, 관리 도구 자체 장애 시 Git 반영이 잠시 중단되는 트레이드오프. 기존 앱을 중지시키는 구조는 아님.
- server ClusterIP, Ingress 없음, TLS 유지. 외부 ALB·공개 도메인·새 EC2를 요구하지 않으나 기존 노드 여유 부족 시 배치 실패 가능.
- 관리 화면은 VPN 연결 후 Mac 터미널 2의 localhost port-forward 사용. 관리자 초기 비밀번호는 화면 공유·로그·Git에 기록하지 않음. private Git 저장소는 UI에서 해당 저장소 Contents read 전용 자격증명 등록, 관리자 PAT·정적 AWS 키를 코드에 넣지 않음.
- GitHub repo-server outbound HTTPS·클러스터 DNS·API 연결 및 이미지 레지스트리 접근 필요. webhook 외부 공개 대신 기본 polling 사용.

## 단계 1 — 로컬 검사와 기존 배포 읽기 비교

실행 위치: Mac 터미널 1, Cloud-v3 저장소. 다음 명령은 파일 렌더만 수행하며 서버 변경 없음.

```bash
ruby scripts/validate-v3-gitops.rb
```

사용자가 직접 실환경 비교할 때만 다음 옵션 사용. kube-context seonjalal-v3, namespace production의 `helm get manifest`와 `kubectl get deployment` 조회만 수행. YAML·Secret 원문은 출력하지 않고 차이가 있으면 필드 경로만 출력. 통과 기대값 Applications=7, managedResources=35, liveRead=true.

```bash
ruby scripts/validate-v3-gitops.rb --live
```

차이가 나오거나 Available 1개 미만이면 적용 중단. 이 검사는 기존 Helm 저장 manifest와의 비교이며 이후 kubectl 변경·API defaulting·Admission 처리까지 전부 대조하는 도구가 아님. 실제 Argo CD Diff를 추가 확인해야 함.

조회 자체가 실패하면 `원인 분류`로 SSO 만료·AWS 프로필·Kubernetes 인증/권한·VPN/API 통신·Release 부재·CLI 옵션 문제를 구분. 원문은 출력하지 않음. `UNKNOWN`이면 원문은 로컬에서만 확인하고 민감정보를 가린 요약으로 추가 조사. 인증 오류를 Release 부재로 판단하거나 다시 설치하지 않음.

## 단계 2 — 코드 검토·Commit·Push와 설치 계획 검토

- 전용 브랜치 feat/v3-argocd 사용. 기존 feat/v3-app-deployment의 커밋을 포함하는 기준이며 PR #124 병합 상태 확인 필요. 중복 PR이면 기존 PR 머지 후 base 재정렬 검토.
- 로컬 코드 검사 통과 → 관련 파일만 Commit·Push → CI 통과 확인. 이 브랜치 push는 새 검증 Workflow만 실행하며 기존 V1/V2 배포는 workflow_dispatch 경계 유지.
- AWS 계정·서울 리전·EKS context 재확인, 시스템 노드 여유 및 설치 대상 CRD/ClusterRole/Pod 계획 검토 후 승인. Chart metadata 조회와 로컬 render는 실제 설치와 구분.
- 공식 Chart 버전 고정 조회 예시: `helm show chart argo-cd --repo https://argoproj.github.io/argo-helm --version 10.17.2`. metadata의 chart/app 버전 불일치 시 중단.
- 검토용 render 예시: `helm template argocd argo-cd --repo https://argoproj.github.io/argo-helm --version 10.17.2 --namespace argocd --kube-version 1.36.0 --values argocd/controller-values.yaml`. 신규 CRD·ClusterRole·NetworkPolicy 및 실제 requests 확인.
- 사용자가 승인한 뒤 제어 도구만 설치. 아직 Application Sync나 앱 변경을 함께 묶지 않음.

## 단계 3 — UI에서 Repository 연결·Application 승계

1. Argo CD Pods Ready 확인 후 `kubectl --context seonjalal-v3 -n argocd port-forward service/argocd-server 18443:443`을 Mac 터미널 2에서 유지. localhost 바인딩 유지.
2. 브라우저 `https://localhost:18443` 접속. 자체 서명 인증서 확인 후 초기 관리자 로그인·비밀번호 변경. UI Settings → Repositories에서 Git URL과 필요 시 저장소 읽기 전용 자격증명 등록, 연결 Successful 확인.
3. Mac 터미널 1에서 로컬 `helm template v3-production-apps argocd/charts/production-apps --namespace argocd`로 AppProject·Application 확인. 수동 모드 확인 후 server dry run과 실제 등록을 분리.
4. 등록 후 UI에서 아직 Sync하지 않고 모든 App의 TARGET MANIFEST와 DIFF 검토. Image·replicas·selector·Secret 참조·스케줄러·AI checksum/strategy 확인.
5. 기존 Service nodePort·clusterIP 등의 API 할당값을 초기화하지 않는 patch인지 확인. selector 변경·scale 0·앱 재생성·강제 삭제가 있으면 중단.
6. Payment부터 1개씩 manual Sync, Prune/Force/Replace 선택하지 않음. Synced/Healthy·Pod UID 변화·공개 health 확인 후 다음 App. AI는 마지막으로 수행.

AI의 desired Pod template이 달라지면 Search 단일 실행 전략 때문에 기존 Pod 종료 후 새 Pod 시작이 필요할 수 있어 일시 중단 가능. 최초 승계는 tracking metadata만 추가하는 최소 차이를 목표로 하고 Pod template 차이가 있다면 별도 교체 영향 승인 후 진행.

## 단계 4 — 자동 CD 활성화·완료 기준

- Cloud develop에 실제 Chart·values·현재 승인 Digest가 모두 존재하는지 먼저 확인. 작업 브랜치 삭제나 merge 여부를 확인하지 않고 revision만 바꾸지 않음.
- 수동 승계 기본값은 유지하고 production-automation.yaml의 revision=develop/automatedSync=true를 코드 리뷰. 활성화 시 마지막 values로 병합.
- bootstrap render 예시: `helm template v3-production-apps argocd/charts/production-apps --namespace argocd --values argocd/production-automation.yaml`. 생성된 App 정의의 targetRevision·enabled·selfHeal·prune 및 리소스 범위 확인 후 dry run·실적용을 분리.
- bootstrap Chart를 사용자 수동으로 다시 렌더·적용. 이 bootstrap 정의 자체는 self-managing App이 아니므로 Git 변경만으로 Application spec이 바뀌지는 않음. 앱의 Chart/values 자동 배포와 bootstrap 변경 적용은 별개.
- 그 뒤부터 develop의 앱 Chart/values/Digest 변경을 Argo CD가 자동 적용, selfHeal 활성. prune은 계속 false. 설정 없는 리소스의 자동 삭제는 이번 목표에서 제외.
- Application syncOptions에도 Prune=false를 명시해 수동 Sync의 기본 삭제 허용 옵션까지 제한. Force/Replace/ServerSideApply는 추가하지 않음.
- 무해한 테스트는 Payment의 최상위 Deployment metadata에 한정한 승인된 Git 변경으로 시작. Pod template은 바꾸지 않고 commit SHA·감지/반영 시각·Synced/Healthy·기존 Pod UID 유지 기록. 되돌림 PR까지 같은 경로로 검증.
- 이미지 배포 검증은 다음 승인 릴리스의 Digest 변경→자동 Sync→새 Pod Ready→공개 서비스 확인으로 수행. 자동 Git 반영 확인과 실제 이미지 배포 성공을 구분.
- Argo CD의 automated sync는 실패 이미지의 자동 Rollback 보장 기능이 아님. 기본 복구는 Git의 잘못된 변경을 revert한 커밋으로 수정. 자동 동기화 활성 상태의 수동 Helm rollback은 다시 Git 상태로 덮일 수 있음.
- 인계 이후 7개 앱에 대한 별도 helm upgrade/rollback/uninstall 금지. Argo CD는 Helm을 template 렌더에 사용하며 기존 Helm Release 이력은 새 Sync로 갱신되지 않음. 기존 Release Secret은 초기에는 보존하되 stale history임을 명시.

## 중단·복구

- 아직 Sync 전: Application 등록·CD 준비 실패는 기존 앱 변경과 별개. 설치 실패만으로 앱을 재배포하지 않음.
- 승인하지 않은 drift/scale/삭제 발견: 자동 동기화부터 중지하고 적용 상태 확인. Argo CD/Helm uninstall 또는 `--cascade` 삭제로 복구하지 않음.
- 이미지·환경변수 문제: Git의 이전 호환 Digest/설정으로 되돌리는 PR 검토. DB·Secret·외부 모델 상태는 Git revert만으로 복구되지 않으므로 별도 검토.
- Redis·RabbitMQ 데이터 삭제·DB Migration·V2 재기동은 이 복구 절차에 포함하지 않음.

## 공식 근거

- [고정 Chart metadata](https://github.com/argoproj/argo-helm/blob/argo-cd-10.17.2/charts/argo-cd/Chart.yaml)
- [고정 Chart values](https://github.com/argoproj/argo-helm/blob/argo-cd-10.17.2/charts/argo-cd/values.yaml)
- [Helm 렌더·Release 이름](https://argo-cd.readthedocs.io/en/stable/user-guide/helm/)
- [Resource tracking](https://argo-cd.readthedocs.io/en/stable/user-guide/resource_tracking/)
- [Automated sync·prune·selfHeal·Rollback 한계](https://argo-cd.readthedocs.io/en/stable/user-guide/auto_sync/)
- [Projects의 리소스·대상 범위](https://argo-cd.readthedocs.io/en/stable/user-guide/projects/)
- [Private repository 연결](https://argo-cd.readthedocs.io/en/stable/user-guide/private-repositories/)
