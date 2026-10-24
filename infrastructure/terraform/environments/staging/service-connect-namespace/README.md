# 스테이징 ECS Service Connect Namespace

- 독립 State `v2/staging/service-connect-namespace/terraform.tfstate`가 `seonjalal-v2-staging-services` HTTP Namespace 하나만 소유한다. AI 없이 BE Main·Payment를 먼저 준비하기 위함(운영 `production/service-connect-namespace`와 같은 구조)
- 기존 운영 계정·서울 리전을 고정한다. Namespace 생성 자체로 ECS Task/Service 또는 트래픽을 기동하지 않는다. `staging/be-split-runtime`의 `namespace_arn` 입력에는 이 Root의 출력을 사용한다.
- **AI 스테이징 적용 전 필수 선행 작업**: `modules/ai_ecs_runtime`은 스테이징에서 같은 이름의 Namespace를 직접 생성한다(`count = var.environment == "staging" ? 1 : 0`). AI Runtime을 Apply하기 전에 AI 모듈이 이 Namespace를 재사용하도록 별도 PR에서 바꿔야 한다. 두 State가 같은 이름을 각각 만들면 이름 충돌로 Apply가 실패한다(AI는 현재 보류).
- 실제 AWS 생성은 병합만으로 일어나지 않는다. Backend 설정 후 저장 Plan에서 Namespace 1개 생성·다른 변경/삭제 0개인지 확인하고 별도 승인받아 Apply한다. 비용 영향은 없다(Namespace 자체는 무료, 연결된 서비스 인스턴스 등록이 없음).

```bash
terraform -chdir=infrastructure/terraform/environments/staging/service-connect-namespace init -backend-config=backend.hcl
terraform -chdir=infrastructure/terraform/environments/staging/service-connect-namespace plan -out=staging-namespace.tfplan
```

## 로컬 검사

```bash
terraform -chdir=infrastructure/terraform/environments/staging/service-connect-namespace validate
terraform -chdir=infrastructure/terraform/environments/staging/service-connect-namespace test
```
