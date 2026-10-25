# 운영 BE·AI ECS 용량

- 운영 BE 애플리케이션 ASG는 서울 2개 App Subnet에 `min_size = desired_capacity = max_size = 2`로 설정한다. 실제 각 AZ에 `t3.large` 1대씩 교체·ECS 등록을 확인했다. BE Split Task/Service는 별도 Root에서 배포한다.
- BE Launch Template 타입은 `t3.large`(2 vCPU, 8 GiB)다. `runtime_compute.instance_type`은 템플릿 목표값이므로 이후에도 실제 EC2 유형과 ECS 등록 상태를 별도로 확인한다.
- 향후 EC2 교체는 저장 Plan·별도 승인 후 ASG Instance Refresh로 수행한다. 새 호스트를 먼저 기동하는 교체에는 ASG 최대 대수를 임시로 늘리고 완료 후 다시 2로 낮춰야 한다. ECS Managed Draining과 BE Service의 `minimumHealthyPercent=100`/`maximumPercent=200`, 새 ECS 호스트 등록·Task/Target Health·AZ 분산을 확인한다. EC2 정상만으로 서비스 무중단을 보장하지 않는다.
- BE Task Main1536MiB+Payment1024MiB는 각 AZ당 2560MiB 예약이 필요하다. `t3.medium` 시절 V1 Task가 있는 2a 호스트의 잔여 메모리2303MiB에는 함께 배치할 수 없었다. 현재 `t3.large` 두 호스트의 ECS 잔여 자원을 실측하기 전에는 BE Split Service를 desired2로 올리지 않는다.
- 이 Root에서 Plan/Apply할 때 이미 기동된 AI 호스트가 축소되지 않도록 `-var='ai_desired_capacity=1'`을 함께 지정한다. 기본값 0으로 Plan하면 AI ASG를 0대로 줄이려 할 수 있다.

- 기존 운영 BE ASG/Capacity Provider와 Cluster 기본 전략은 유지한다. AI 전용 ASG·Capacity Provider만 같은 Terraform State에 추가한다. Cluster Capacity Provider 연결은 이 Root 한 곳에서만 관리한다.
- AI 호스트는 서울 A Private App Subnet `subnet-08c2017235a4a56da`에만 배치한다. BE와 호스트를 공유하지 않는다.
- 초기 `ai_desired_capacity = 0`, `min_size = 0`, `max_size = 1`. 코드 병합이나 기본값 Apply만으로 EC2가 기동하지 않는다. 별도 승인 후 `ai_desired_capacity = 1`로 변경한다.
- 초기 호스트는 x86_64 `t3.medium`(2 vCPU, 4 GiB), 암호화된 gp3 30 GiB이다. 현재 AI App Task 예약값은 2 vCPU·4 GiB이고 호스트가 `ECS_RESERVED_MEMORY=512` MiB를 따로 예약하므로 이 조합으로는 Task를 배치할 수 없다. 컨테이너별 메모리 한도 합계도 3840 MiB라 Task 예약값만 낮춰서는 해결되지 않는다. AI Task·컨테이너 메모리 실측/조정 또는 호스트 상향이 승인되기 전까지 AI Service `desired_count=0`을 유지한다.
- ECS Managed Scaling은 끈다. 서비스 요청만으로 ASG가 0대에서 자동 기동하지 않는다. AI Task를 실행하기 전에 EC2 1대가 Cluster에 등록되어야 한다.
- 기존 운영 ECS 호스트와 같은 Node IAM Profile·보안그룹을 사용한다. SSH 인바운드는 없고 SSM으로 점검한다. AI Task의 자체 SG·DB Client SG는 `production/ai-runtime` Root에서 관리한다.
- `aws_ecs_cluster_capacity_providers.production`은 기존 BE 공급자와 AI 공급자를 함께 등록하되 기본 전략은 BE 공급자로 유지한다. 저장 Plan에서 기존 BE Service·ASG·Task Definition·Listener의 교체/삭제가 없어야 한다.
- 최초 Apply는 새 Launch Template·ASG(0대)·Capacity Provider와 Cluster 연결 변경만 예상한다. 실제 비용과 서비스 영향은 저장 Plan 검토 후 승인한다.
- 기존 운영 Root가 이미 관리하는 공급자 연결을 다른 Terraform State에서 중복 관리하지 않는다.

## 호스트 수 변수 (`node_capacity`)

- BE ASG의 `min_size = desired_capacity = max_size`는 `node_capacity`(기본 2, 허용 2~4)로 정한다. 기본값에서는 기존과 동일하다.
- 블루-그린 전환처럼 새 Task를 옆에 띄울 때만 `-var='node_capacity=4'`로 임시 증설하고, 전환과 정리가 끝나면 2로 되돌린다. 축소는 ECS Managed Draining이 Task를 옮긴 뒤 호스트를 종료한다.
- plan·apply 때 `ai_desired_capacity`와 함께 지정한다. 예: `-var='ai_desired_capacity=1' -var='node_capacity=4'`.
