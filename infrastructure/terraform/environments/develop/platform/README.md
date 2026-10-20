# V2 Develop ECS Platform

이 Root는 기존 Develop 단일 EC2/Compose Runtime을 변경하지 않고 다음 ECS 공용 기반만 관리한다.

- Amazon ECS Cluster
- 두 Private App Subnet에 걸친 EC2 Auto Scaling Group
- ECS-optimized Amazon Linux 2023 Launch Template
- Managed Draining이 켜진 Application Capacity Provider. 빈 초기 단계의 Managed Scaling/Termination Protection 비활성화
- SSM 전용 관리 경로(SSH Inbound 없음)

## 적용 전제

초기 적용은 `min=0`, `desired=0`, `max=6`인 빈 ASG와 Cluster/Capacity Provider만 생성한다. EC2/EBS와 NAT는 생성하지 않는다. 기존 Foundation State에 `app_egress` Output이 없어도 빈 ASG 생성 가능.

EC2 기동 전 ECS 제어면 접근 경로 구성 필요. NAT는 별도 작업으로 생성하며 NAT Instance/Gateway 선택은 후속 결정. 현재 기동 Gate는 Foundation의 `single` 또는 `per_az`만 인식하므로 별도 Egress Root 도입 시 해당 Output 계약 연결 필요.

`managed_scaling_enabled=false`로 자동 증설 비활성화. 초기 적용 후 기존 `target_capacity=80` Managed Scaling Alarm이 Task 없는 상태에서도 desired를 0에서 2로 올린 기록 확인. Egress/Workload 준비 후 Managed Scaling과 Termination Protection을 함께 활성화하고 `target_capacity=100`으로 시작. ASG의 기존 Instance별 Scale-in Protection은 별도 확인 필요. Egress 구성 전 Task/Service 실행 또는 ASG 수동 Scale-out 금지. 이 단계는 두 AZ Subnet에 연결된 빈 Capacity 기반 생성 단계다.

근거: [AWS ASG Capacity Provider와 빈 ASG](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/asg-capacity-providers.html)

## 의도적으로 아직 포함하지 않은 것

- ALB와 Main/Payment ECS Service/Task Definition
- RDS와 Flyway 일회성 Migration Task
- AI 전용 Capacity Provider와 AI Service
- Route 53/ACM/HTTPS Listener

서비스를 먼저 만들면 현재 미확정인 RDS Endpoint, Secret JSON Key, 인증서/도메인, 이미지 Tag 계약을 임시값으로 고정하게 되므로 분리했다. AI는 Dockerfile과 Health/Port/CPU·Memory 계약을 받은 뒤 전용 Capacity Provider에 `desired_count = 2`로 추가한다.

## 검증 및 적용 순서

실제 AWS 변경 명령은 인프라 담당자가 Plan을 검토한 뒤 직접 실행한다.

1. 이 디렉터리 전용 Backend Key와 Foundation State 참조 설정
2. `terraform init` 및 저장된 `terraform plan` 생성
3. 두 App Subnet, ASG 최소/희망 0, 최대 6 및 신규 리소스만 생성되는지 검토
4. 승인 후 Apply
5. Cluster Task/Service/Container Instance 0개와 Capacity Provider 연결 확인

과거 `foundation-nat.tfplan`은 이번 적용 대상에서 제외. Bootstrap/Runtimes State와 Platform State 분리 유지.

기존 `environments/develop/runtime`은 전환 완료 전까지 유지한다.
