# 운영 AI 전용 ECS 용량

- 기존 운영 BE ASG/Capacity Provider와 Cluster 기본 전략은 유지한다. AI 전용 ASG·Capacity Provider만 같은 Terraform State에 추가한다. Cluster Capacity Provider 연결은 이 Root 한 곳에서만 관리한다.
- AI 호스트는 서울 A Private App Subnet `subnet-08c2017235a4a56da`에만 배치한다. BE와 호스트를 공유하지 않는다.
- 초기 `ai_desired_capacity = 0`, `min_size = 0`, `max_size = 1`. 코드 병합이나 기본값 Apply만으로 EC2가 기동하지 않는다. 별도 승인 후 `ai_desired_capacity = 1`로 변경한다.
- 초기 호스트는 x86_64 `t3.medium`(2 vCPU, 4 GiB), 암호화된 gp3 30 GiB이다. 현재 AI App Task 예약값은 2 vCPU·4 GiB이고 호스트가 `ECS_RESERVED_MEMORY=512` MiB를 따로 예약하므로 이 조합으로는 Task를 배치할 수 없다. 컨테이너별 메모리 한도 합계도 3840 MiB라 Task 예약값만 낮춰서는 해결되지 않는다. AI Task·컨테이너 메모리 실측/조정 또는 호스트 상향이 승인되기 전까지 AI Service `desired_count=0`을 유지한다.
- ECS Managed Scaling은 끈다. 서비스 요청만으로 ASG가 0대에서 자동 기동하지 않는다. AI Task를 실행하기 전에 EC2 1대가 Cluster에 등록되어야 한다.
- 기존 운영 ECS 호스트와 같은 Node IAM Profile·보안그룹을 사용한다. SSH 인바운드는 없고 SSM으로 점검한다. AI Task의 자체 SG·DB Client SG는 `production/ai-runtime` Root에서 관리한다.
- `aws_ecs_cluster_capacity_providers.production`은 기존 BE 공급자와 AI 공급자를 함께 등록하되 기본 전략은 BE 공급자로 유지한다. 저장 Plan에서 기존 BE Service·ASG·Task Definition·Listener의 교체/삭제가 없어야 한다.
- 최초 Apply는 새 Launch Template·ASG(0대)·Capacity Provider와 Cluster 연결 변경만 예상한다. 실제 비용과 서비스 영향은 저장 Plan 검토 후 승인한다.
- 기존 운영 Root가 이미 관리하는 공급자 연결을 다른 Terraform State에서 중복 관리하지 않는다.
