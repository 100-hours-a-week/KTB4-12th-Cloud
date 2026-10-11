# ADR-001: V2 Application Compute로 ECS on EC2 채택

- 상태: 승인
- 최초 작성일: 2026-09-16
- 승인 기준일: 2026-10-06
- 범위: V2 Backend Application Compute와 Load Balancing

## 결정

V2 Backend는 **Amazon ECS on EC2**로 실행한다.

- 두 AZ의 Private App Subnet에 ECS Task를 배치한다.
- EC2 Capacity는 Multi-AZ Auto Scaling Group과 ECS Capacity Provider로 관리한다.
- Internet-facing ALB가 HTTPS를 종료하고 정상 Task의 Private IP로 요청을 분산한다.
- Service는 최소 두 Task를 유지하고 AZ `spread`를 `binpack(memory)`보다 먼저 적용한다.
- ECS Service Auto Scaling과 Capacity Provider Scaling을 분리해 Task 수와 EC2 Capacity를 조정한다.
- ECS Rolling Deployment와 Deployment Circuit Breaker 자동 Rollback을 사용한다.
- Task Image는 ECR Private Repository의 Commit SHA Tag와 Digest로 고정한다.

이 결정은 [4단계 Wiki 설계](https://github.com/100-hours-a-week/KTB4-12th-wiki/wiki/%EC%84%A4%EA%B3%84-4%EB%8B%A8%EA%B3%84:-%EC%BB%A8%ED%85%8C%EC%9D%B4%EB%84%88-%EA%B8%B0%EB%B0%98-%EC%84%9C%EB%B9%84%EC%8A%A4-%ED%99%95%EC%9E%A5-%EB%B0%8F-%EC%9A%B4%EC%98%81-%EC%9E%90%EB%8F%99%ED%99%94-%EC%84%A4%EA%B3%84)의 결론을 Cloud 저장소의 기준 결정으로 옮긴 것이다.

## 문제

V1은 Frontend, Backend와 MySQL을 단일 `t3.medium` EC2의 Docker Compose에서 실행한다. 이 구조는
Application Process, Host와 AZ 장애가 전체 서비스 중단으로 확대되고, Component별 수평 확장과
정상 Version을 유지한 Rolling Deployment가 없다.

V1 혼합 부하 시험에서 다음을 확인했다.

- 혼합 30 RPS 지속과 60 RPS Spike는 오류 없이 처리했다.
- 약 189 RPS부터 5초 Timeout이 발생했다.
- Stress 구간에서 Host CPU 100%, Backend CPU 120.56%, MySQL CPU 75.32%가 관측됐다.
- 첫 구조적 한계는 단일 Host의 공유 CPU와 Backend 처리 대기였다.
- MySQL Slow Query, OOM과 Container Restart 증거는 없었다.

근거: `docs/2026-10-02-v1-load-test-final-report.md`

## 판단 기준

1. Task, EC2와 Application AZ 장애에서 자동 교체와 남은 요청 경로를 제공하는가.
2. 배포 중 정상 Task를 유지하고 실패 배포를 자동 중단·Rollback할 수 있는가.
3. Component별 CPU·Memory와 확장 단위를 분리할 수 있는가.
4. V1 Docker Image를 큰 변경 없이 옮기고 동일 Image 비교 시험을 수행할 수 있는가.
5. 월 예산과 Cloud 담당자 한 명의 운영 범위에서 Host 비용을 통제할 수 있는가.
6. 30·300·600 RPS 입력값을 실제 측정으로 교체할 수 있는가.

## 대안 비교

| 대안 | 장점 | 제외·비선택 이유 |
|---|---|---|
| EC2 + Docker Compose | 현재 전환비용이 가장 낮음 | Task 배치·복제·Rolling·Desired State를 직접 구현해야 함 |
| EC2 + ASG만 사용 | Host 자동 교체와 수평 증설 가능 | Application 배포·Health 기반 Task 교체·배치를 별도로 구현해야 함 |
| **ECS on EC2** | ECS Service 기능과 EC2 Capacity 비용 통제를 함께 사용 | AMI·Agent·Patch·Draining·유휴 Capacity를 직접 운영해야 함 |
| ECS on Fargate | Host 운영 책임과 Instance Capacity 관리 감소 | 장기 실행 Task 비용과 V2 학습·검증 목표를 고려해 이번 Version에서는 비선택 |
| EKS | Kubernetes 생태계와 확장성 | 현재 서비스 규모에서 Control Plane·Cluster 운영 복잡도를 정당화할 요구가 없음 |

Fargate가 기능적으로 부족해서 제외한 것은 아니다. 두 AZ의 장기 실행 Capacity를 EC2에 조밀하게
배치해 비용을 통제하고, ECS와 ASG의 두 단계 Scaling과 Host 운영을 직접 검증하는 것을 V2 범위로
선택했다. 실제 비용 또는 운영 부담이 예상보다 크면 Fargate를 다시 비교한다.

## 초기 검증값

| 항목 | 초기값 | 의미 |
|---|---:|---|
| EC2 Type | `t3.large` | 부하 시험 전 검증용, 확정 사양 아님 |
| ASG | `min=2`, `desired=2`, `max=6` | 두 AZ 최소 Capacity 검증 |
| Task CPU·Memory | 1 vCPU·2 GiB | 기동·부하 시험용 초기값 |
| Task Count | `min=2`, `max=10` | AZ별 최소 한 Task와 Scale-out 검증 |
| Rolling | `minimumHealthyPercent=100`, `maximumPercent=200` | 정상 Task 유지 배포 검증 |
| Placement | AZ `spread` → `binpack(memory)` | 장애 분산 후 유휴 Capacity 감소 |

이 값은 Capacity 확정값이나 600 RPS 보장값이 아니다. V1과 같은 30·60 RPS 회귀 시험, 300 RPS
Stress와 600 RPS Burst에서 p95·p99, 오류율, Task CPU·Memory, Hikari/DB Connection, Scale-out
시간과 비용을 측정한 뒤 조정한다.

## 결과와 책임

### 얻는 것

- Desired Task 수 유지와 비정상 Task 자동 교체
- ALB Health 기반 요청 분산과 변하는 Task IP 자동 등록
- 정상 Task를 유지하는 Rolling Deployment와 Circuit Breaker Rollback
- Service Auto Scaling과 EC2 Capacity Scaling 분리
- Task Definition·Image Digest 기반 재현 가능한 배포

### 감수하는 것

- ECS-Optimized AMI, ECS Agent, OS 보안 Patch와 Instance Refresh 운영
- Task 증가보다 EC2 준비가 늦을 수 있는 두 단계 Scale-out
- AZ별 최소 EC2의 상시 비용과 낮은 부하에서의 유휴 Capacity
- Capacity Provider, ASG, Task Placement와 Draining을 함께 진단해야 하는 복잡도

## 구현 순서

1. Terraform Remote State, 환경 경계와 공통 Tag
2. VPC Public/App/Data Subnet, Route와 Security Group
3. IAM Role, Secret, KMS와 ECR
4. ALB, Target Group과 ACM
5. Launch Template, ASG, ECS Cluster와 Capacity Provider
6. Task Definition, ECS Service와 Auto Scaling
7. Develop CD와 Circuit Breaker Rollback Drill
8. 관측·장애·부하 시험 후 초기 용량 보정

상세 Backlog와 완료 조건은 `docs/V2_IMPLEMENTATION_PLAN.md`를 따른다.

## 검증 항목

- 두 Task가 서로 다른 AZ에서 ALB Health를 통과한다.
- Task Kill과 EC2 종료 후 Desired State로 자동 복구한다.
- 한 AZ의 Application Capacity 제거 시 남은 AZ가 평시 30 RPS를 처리한다.
- Scale-out/Scale-in 중 Request Error, Pending 시간과 Draining 결과를 기록한다.
- 잘못된 Image 배포가 Circuit Breaker로 직전 정상 Revision에 Rollback된다.
- 새 Launch Template의 Instance Refresh 중 최소 Application Capacity를 유지한다.
- 같은 V1 Image와 Fixture로 V1/V2 30·60 RPS를 반복 비교한다.

## 재검토 조건

다음 중 하나가 확인되면 ECS on Fargate 또는 다른 Compute 방식을 다시 비교한다.

- 상시 EC2 유휴 비용이 Fargate 동등 조건보다 지속적으로 높다.
- AMI·Agent·Patch·Draining 운영이 허용 가능한 배포·복구시간을 반복해서 초과한다.
- 예측하지 못한 Burst에서 EC2 준비 시간이 SLO를 충족하지 못하고 Scheduled Scaling으로 보완할 수 없다.
- Workload가 짧고 간헐적인 Task 중심으로 바뀌어 EC2 상시 Capacity의 이점이 사라진다.
- 팀의 운영 인력과 보안 Patch 책임이 ECS on EC2를 유지할 수 없는 수준으로 바뀐다.

## 공식 근거

- [ECS Auto Scaling Group Capacity Provider](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/asg-capacity-providers.html)
- [ECS Service Load Balancing](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/service-load-balancing.html)
- [ECS Deployment Circuit Breaker](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/deployment-circuit-breaker.html)
- [ECS Task Placement Strategy](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task-placement-strategies.html)
- [ECS Managed Instance Draining](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/managed-instance-draining.html)
