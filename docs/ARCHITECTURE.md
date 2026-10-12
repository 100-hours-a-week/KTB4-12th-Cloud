# 선잘알 Cloud Architecture

- 기준일: 2026-10-11
- 범위: Local·V1 Benchmark·V2 Develop·Production
- 표기:
  - **구현됨**: 실제 구성과 검증 증거가 있음
  - **계획**: 아직 코드·AWS에 반영되지 않음

## 1. 환경 모델

| 환경 | 목적 | 상태 | 배포 기준 |
|---|---|---|---|
| Local | 개인 개발·테스트 | 구현됨 | 작업 Branch |
| V2 Develop | 팀 통합·빠른 기능 확인 | 자동 배포 구현·검증 완료 | `develop-<sha>` Candidate Image |
| V1 Benchmark | V1·V2 동일 Image 비교와 부하 시험 | Domain·HTTPS 구현됨 | 고정 Image Digest·Fixture |
| Production | 실제 사용자 서비스 | 구현됨 | Semantic Version Release |

V1에서는 별도 Staging을 운영하지 않는다. V1 Benchmark는 기존 구조의 비교 기준만 담당한다.
운영 유사 QA·부하·Release 검증은 향후 V2 Staging을 별도로 구성하고 시험 중 Artifact Digest를 고정한다.

## 2. V1 Runtime Architecture

### 2.1 공통 실행 단위

```text
Client
  │ HTTPS
  ▼
Host Nginx :443
  │ http://127.0.0.1:8081
  ▼
Frontend Container (Nginx, static SPA)
  │ /api/* reverse proxy
  ▼
Backend Container :8080
  ├─ MySQL Container :3306 + Docker named volume
  └─ S3 Product Image Bucket (EC2 Instance Role)
```

- Host Nginx가 Public TLS를 종료한다.
- Frontend Port는 Host Loopback에만 Bind한다.
- Backend와 MySQL은 Docker Bridge Network에만 노출한다.
- Backend의 AWS 접근은 정적 Access Key가 아닌 EC2 Instance Role을 사용한다.

### 2.2 V1 Benchmark

| 항목 | 값 |
|---|---|
| Domain | `v1-benchmark.seonjalal.com` |
| EC2 | `t3.medium`, Amazon Linux 2023, `ap-northeast-2a` |
| Path | `/opt/seonjalal-develop` |
| Compose | `compose.yaml` + `compose.develop.yaml` |
| Environment | `.env.develop` |
| Project | `seonjalal-develop` |
| Database Volume | `seonjalal-develop-db-data` |
| 관리 접속 | SSM Session Manager |
| Web 접근 | Security Group의 승인된 IPv4 `/32` |

현재 Frontend·Backend·MySQL Container Health와 V1 Benchmark HTTPS Browser 화면까지 검증했다.
Elastic IP가 없어 Stop·Start 후 Route 53 A 레코드 갱신이 필요하며, 인증서는 수동 DNS-01 방식이다.

### 2.3 V2 Develop

| 항목 | 값 |
|---|---|
| 접근 이름 | Tailscale MagicDNS |
| EC2 | `t3.medium`, Amazon Linux 2023, `ap-northeast-2a` |
| Path | `/opt/seonjalal-develop` |
| Runtime | Frontend·Main Backend·Payment Server·MySQL Docker Compose |
| 관리 접속 | SSM Session Manager |
| Web 접근 | Tailscale Serve HTTPS, Tailnet 전용 |
| Public Ingress | 0개 |

FE·BE `develop` Merge의 ECR Image 발행, Parameter Store 희망 SHA 갱신, 전용 SSM Document 기반
재조정과 네 Container Health, Tailnet HTTPS와 Browser 화면을 검증했다. EC2 중지 상태의 Merge는
희망 SHA를 보존하고 다음 부팅 시 systemd oneshot으로 최신 조합을 반영하도록 구성했다.

### 2.4 Production

- 기존 Production EC2, Compose와 수동 승인 CD를 유지한다.
- `www.seonjalal.com`이 서비스 진입점이며 Apex는 `www`로 Redirect한다.
- Develop 자동 배포 작업은 Production Workflow, GitHub Environment, IAM Role, SSM Target과
  분리한다.

## 3. Network와 Trust Boundary

| 경계 | 정책 |
|---|---|
| EC2 관리 | Public SSH 22를 닫고 SSM 사용 |
| Public TLS | Host Nginx 443만 사용 |
| Frontend Origin | `127.0.0.1:8081` Bind |
| Backend | Compose Network 내부 8080 |
| MySQL | Compose Network 내부 3306 |
| AWS API | EC2 Instance Role의 최소 권한 |
| Secret | Git 미추적 `.env`, GitHub Environment Secret, AWS 단기 자격 증명 |

V1 Benchmark는 시험 Window의 승인된 `/32`만 허용한다. V2 Develop은 Tailscale 장치 Identity를
사용하지만 팀원 초대 전 HTTPS 443 전용 Tag·Grant 최소 권한 적용이 필요하다.

검토 우선순위:

1. Tailscale 또는 Cloudflare Tunnel + Access
2. AWS Native가 필수이면 IAM Identity Center + SSM Port Forwarding
3. 팀원별 `/32`는 짧은 임시 방안
4. AWS Client VPN은 V1 비용·운영 복잡도 때문에 후순위

## 4. Delivery Architecture

### 4.1 현재

```text
FE/BE main Push
  → CI 검증
  → Semantic Version Image 발행

Cloud Production workflow_dispatch
  → GitHub OIDC
  → AWS SSM Run Command
  → Production Compose 배포
  → Health·Smoke
  → 실패 시 Image Rollback
```

Develop은 FE·BE 각 Repository의 `develop` Merge가 Image 발행과 자동 배포를 시작한다. 서버 제어 구현과
Rollback 계약은 Cloud Repository가 소유한다.

### 4.2 구현됨: Develop 전용 자동 배포

```text
FE develop Merge                 BE develop Merge
  → Test·Build                     → Test·Build
  → FE ECR Image Push              → Main·Payment ECR Image Push
  → FE 희망 SHA 갱신               → BE 희망 SHA 갱신
           └──────────── Candidate Pair ────────────┘
                              │
                              ▼
                 전용 SSM Document 실행
                      → Host flock
                      → .env Image URI 원자적 교체
                      → Compose Pull·Up
                      → Health·Smoke
                      → 실패 시 이전 Candidate Rollback
```

### 4.3 Production 보호 Invariant

- Develop CD는 Production Instance ID, Domain, Environment Name을 허용하지 않는다.
- Develop과 Production의 GitHub Environment, OIDC Role, SSM Target을 분리한다.
- 기존 Production Workflow의 Trigger와 입력 계약은 Develop 작업에서 수정하지 않는다.
- Candidate Tag는 Commit SHA 기반 Immutable Tag로 만들고 `latest`를 배포 기준으로 쓰지 않는다.
- Production은 Develop에서 검증한 동일 Digest를 Release로 승격한 뒤 수동 승인한다.
- QA·부하 시험 중에는 Develop 배포를 동결한다.

## 5. Stateful Data

- V1 Develop과 Production은 서로 다른 MySQL과 Docker Named Volume을 사용한다.
- Develop Fixture는 운영 개인정보나 운영 Dump를 복사하지 않고 합성 데이터로 만든다.
- Fixture 생성과 Reset은 반복 가능하고 시험 ID로 구분할 수 있어야 한다.
- Flyway Migration은 Image Rollback과 별개다. Container Rollback이 DB Schema·Data를 자동으로
  되돌리지 않으므로 파괴적 Migration 전 Backup과 Forward-fix 전략이 필요하다.
- 과거 `seonjalal-staging-db-data`는 현재 복구 대비로 보존 중이며, 승인 전 삭제하지 않는다.

## 6. Observability Architecture

### 6.1 현재

- Backend Actuator Health
- Docker Container Health Check
- CloudWatch Agent를 사용한 임시 Host Metric 수집 경험
- k6 결과 파일 기반 시험 기록

Portainer는 실시간 단일 화면 확인에는 유용했지만 장기 Metric, Dashboard, 여러 Container 비교와
부하 시험 상관분석 요구를 충족하지 못해 제거 대상으로 결정했다.

### 6.2 목표

```text
Node Exporter ───────┐
cAdvisor ────────────┤
Backend Micrometer ──┤
MySQL Exporter ──────┼──> Prometheus ──> Grafana Dashboard·Alert
Nginx Exporter ──────┘

Nginx/Application structured logs
  ──> Log collection agent ──> Loki ──> Grafana Explore

k6
  ──> Prometheus remote write 또는 결과 Artifact
```

첫 구현 전에 다음을 확정한다.

- Metric Label Cardinality
- Scrape Interval과 보존기간
- Log의 Token·Cookie·Password Redaction
- Dashboard와 Alert의 실제 사용 목적
- 관측 Stack의 CPU·Memory·Disk Resource Budget

Develop `t3.medium` 한 대에 전체 관측 Stack을 함께 실행하면 부하 시험 결과가 왜곡될 수 있다.
동일 Host로 시작할 경우 Resource Limit과 관측 Stack 자체 사용량을 기록하고, 간섭이 크면 별도
Instance 또는 관리형 서비스로 분리한다.

## 7. SLI·SLO와 부하 시험

```text
User Journey 정의
  → 임시 SLI·SLO
  → Metric·Log 계측
  → Fixture·Reset
  → Smoke
  → Baseline
  → Capacity
  → Breakpoint
  → 측정 결과로 SLO·Release Gate 보정
```

임시 SLI가 수집 항목을 결정하며, SLO 수치는 실제 Baseline과 사용자 요구를 근거로 확정한다.
Host CPU만으로 서비스 품질을 판단하지 않고 사용자 요청 성공률·Latency와 자원 지표를 같은
시간축에서 분석한다.

## 8. V2와의 관계

V2의 ECS·ASG·Fargate, RDS Multi-AZ, Auto Scaling 방식과 용량은 아직 확정하지 않는다. V1의
관측과 부하 시험 결과를 V2 의사결정 입력으로 사용한다. 세부 검토는
`docs/decisions/001-v2-infrastructure.md`를 따른다.
