# ADR-003: V2 범위·복구 목표·예산 기준

- 상태: 승인 전 실행 기준
- 작성일: 2026-10-09
- Owner: Cloud(Peter)
- 팀 검토 기한: 2026-10-12
- 범위: V2 Develop 구축과 Production 전환의 Critical Path

## 결정 요약

| 항목 | 실행 기준 | 상태·검증 방법 |
|---|---|---|
| 우선순위 | V2 Terraform Foundation → 실행 기반 → RDS/ECS 전환을 먼저 구현한다. | 확정 |
| V1 부하 시험 | 추가 시험은 V2 Foundation의 선행 조건으로 삼지 않는다. V1 전용 Instance는 V2 비교 시험 전까지 보존한다. | 확정 |
| V2 비교 시험 | 같은 Image와 Scenario로 V1/V2를 비교한 뒤 Code 최적화 Image를 별도 시험한다. | B2에서 검증 |
| SLO | 기존 rolling 30일 가용성 99.5%와 읽기 응답시간 잠정 SLO를 최소 기준으로 유지한다. | 운영 계측 후 보정 |
| 600 RPS | 용량 보장값이 아니라 V2 Burst 실패·회복 시험 입력으로만 사용한다. | 확정 |
| 월 예산 | 월 300,000원 + 승인된 추가 비용을 계획 기준으로 사용한다. | Apply 전 비용표 승인 |
| Production RTO | 초기 목표 60분 이내 | 복원·전환 Drill에서 실측 후 승인 |
| Production RPO | 초기 목표 5분 이내 | RDS PITR와 정합성 검증 후 승인 |
| Redis·Queue | 현재 Critical Path에서 제외한다. 요구가 확인될 때 별도 ADR로 추가한다. | 기본안, 2026-10-12 팀 확인 |
| AI Database | PostgreSQL/pgvector 신규 구축은 현재 Critical Path에서 제외한다. 기존 AI 연동 계약만 보존한다. | 기본안, 2026-10-12 팀 확인 |

`승인 전 실행 기준`은 Terraform Code 작성과 Local 정적 검증을 진행할 수 있다는 뜻이다. 실제 AWS
Resource 생성, Production 전환 또는 비용 약정은 각 단계의 Plan과 비용표를 검토한 뒤 별도 승인한다.

## 근거와 해석

### 이미 완료된 SLI·SLO와 부하 시험

- `load-tests/SLI-SLO.md`에 사용자 경계 가용성, 핵심 읽기 응답시간과 Journey SLI가 정의돼 있다.
- 현재 SLO는 설계 완료·운영 검증 전 상태다. 따라서 새 수치를 만드는 대신 실제 계측으로 검증한다.
- V1 시험은 9개 API의 Smoke, 개별 Baseline, 혼합 Baseline, Capacity, Spike와 Stress를 수행했다.
- 약 189 RPS는 최초 Timeout 관측 지점이지 안전 처리량이나 V2 용량 산정값이 아니다.
- 쓰기·전체 Journey·장애 복구를 포함한 최종 한계 시험은 관측 가능한 V2가 준비된 뒤 수행하는 편이
  Infrastructure 효과와 Code 최적화 효과를 분리하는 데 적합하다.

### 비용 기준

월 300,000원은 경보가 아니라 계획 기준이다. `+a`는 다음 조건을 모두 만족할 때만 사용한다.

1. V2 Critical Path 또는 비교 시험에 필요한 비용이다.
2. 월 환산 비용, 사용 기간과 종료·축소 방법이 기록돼 있다.
3. Apply 전에 사용자가 명시적으로 승인한다.

비용표에는 최소한 EC2/ASG, NAT Gateway와 Data 처리, ALB, RDS, EBS, CloudWatch Log, State S3와
향후 Monitoring Host를 포함한다. 서울 Region의 실제 값은 Apply 직전
[AWS Pricing Calculator](https://calculator.aws/)로 다시 계산한다.

### RTO·RPO의 위치

RTO 60분과 RPO 5분은 아직 달성했다고 주장할 수 없는 초기 목표다. RDS PITR 복원, Application
Smoke, ECS Rollback과 Data 정합성 확인 시간을 포함한 Drill 결과로 승인 또는 조정한다.

공식 근거:

- [RDS 자동 Backup과 Point-in-time recovery](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_WorkingWithAutomatedBackups.html)
- [ECS Deployment Circuit Breaker](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/deployment-circuit-breaker.html)

## V2 Critical Path

```text
A3 Foundation Network IaC
  → A4 IAM·Secret·ECR·ALB 실행 기반
  → B1 RDS Migration Rehearsal
  → B2 ECS Runtime·Rollback
  → A5 Develop CI/CD
  → A6 최소 관측
  → B3 통합 Cutover
  → B4 중앙 관측 고도화와 V1/V2 비교 시험
```

Redis, Queue, 신규 AI Database와 추가 V1 한계 시험은 위 경로를 막지 않는다. Application 요구가
확인되면 영향 받는 Security Group, Secret, Backup, 관측과 비용을 별도 Plan으로 추가한다.

## 일정과 완료 조건

| 작업 | Owner | 목표일 | 완료 조건 |
|---|---|---:|---|
| A2 팀 확인 | Cloud(Peter), PL/BE 확인 | 2026-10-12 | 기본안 변경 여부와 승인자를 기록한다. |
| A3 Foundation Code | Cloud(Peter) | 2026-10-09 | `fmt`, `validate`, 정적 검사와 안전한 Plan을 통과한다. |
| A4 실행 기반 Code | Cloud(Peter) | 2026-10-12 | IAM/ECR/ALB Plan과 비용 영향을 검토한다. |
| B1 RDS Rehearsal 설계 | Cloud(Peter), BE 협의 | 2026-10-14 | Data 검증·중단·Rollback Runbook이 있다. |
| B2 ECS Develop 기동 | Cloud(Peter) | 2026-10-16 | 두 AZ Task, Health와 실패 배포 Rollback을 검증한다. |

날짜는 1인 Cloud 담당의 작업 순서 기준이며 팀 의존성이나 Plan 결과가 바뀌면 이유와 새 기한을
`docs/CURRENT_STATUS.md`에 기록한다.

## 제외한 대안

| 대안 | 제외 이유 |
|---|---|
| V1 전체 한계 시험을 다시 끝낸 뒤 V2 착수 | 현재 V2 구조·관측이 없어 V2 용량 결정을 확정할 수 없고 Critical Path를 지연시킨다. |
| 600 RPS를 V2 초기 용량 보장값으로 사용 | 실제 Workload, 쓰기 비율과 Scale-out 시간이 검증되지 않았다. |
| Redis·Queue·AI DB를 모두 선구축 | Application 요구와 운영 책임이 확정되지 않아 비용과 장애 범위를 불필요하게 늘린다. |
| RTO·RPO를 달성 완료로 표시 | 복원 Drill과 정합성 실측 증거가 아직 없다. |

## 재검토 조건

- BE 또는 AI Runtime이 Redis, Queue나 PostgreSQL/pgvector 없이는 기동할 수 없음이 확인된다.
- 월 예상 비용이 300,000원을 넘고 추가 비용의 승인 근거가 없다.
- RDS 복원 또는 ECS Rollback Drill이 초기 RTO·RPO를 반복해서 초과한다.
- V2 비교 시험에서 기존 잠정 SLO가 사용자 요구 또는 실제 분포와 맞지 않는다.
