# 운영 ECS Service Connect Namespace

- 독립 State `v2/production/service-connect-namespace/terraform.tfstate`가 `seonjalal-v2-production-services` HTTP Namespace 하나만 소유한다. BE Main·Payment가 AI Task보다 먼저 준비될 수 있도록 한다.
- 기존 운영 계정·서울 리전을 고정한다. Namespace 생성 자체로 ECS Task/Service 또는 트래픽을 기동하지 않는다. BE Split Root의 `namespace_arn`에는 이 Root의 출력을 사용한다.
- 현재 AI 모듈도 같은 이름의 Namespace를 직접 생성하도록 작성돼 있다. 운영 AI Runtime을 Apply하기 전에 AI 모듈을 이 ARN을 재사용하도록 별도 PR에서 수정해야 한다. 두 State가 같은 이름의 Namespace를 각각 생성하면 안 된다.
- 실제 AWS 생성은 병합만으로 일어나지 않는다. Backend 설정 후 저장 Plan에서 Namespace 1개 생성·다른 변경/삭제 0개인지 확인하고 별도 승인받아 Apply한다.
