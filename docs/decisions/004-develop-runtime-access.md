# ADR-004: Develop Runtime와 접근 경계

- 상태: 구현, 최초 배포와 Develop CD 자동화 검증 완료
- 결정일: 2026-10-10
- 범위: 빠른 팀 통합을 위한 신규 Develop Runtime과 기존 V1 비교 환경의 분리

## 결정

- 기존 `seonjalal-v1-dev` EC2는 삭제하거나 새 Develop로 덮어쓰지 않고 V1 Benchmark 기준점으로
  보존한다.
- 기존 V1 Benchmark는 평상시 중지하고 비교 시험 Window에만 시작한다.
- 기존 V1 Benchmark의 Public 진입 이름은 `v1-benchmark.seonjalal.com`으로 분리하고 현재 작업자
  Public IPv4 `/32`만 Security Group과 Nginx Allowlist에서 허용한다.
- 신규 Develop은 Public Route 53 레코드를 사용하지 않고 Tailscale MagicDNS와 Serve HTTPS를 사용한다.
- 신규 Develop은 V2 Foundation VPC의 `public-a` Subnet에 단일 EC2로 구성한다.
- 신규 Develop EC2에는 Public IPv4를 할당하지만 Security Group Ingress 규칙은 만들지 않는다.
- 관리 경로는 AWS Systems Manager, 팀 Web 접근은 Tailscale Serve와 Tailnet ACL만 사용한다.
- Bootstrap에서 Tailscale 공식 Amazon Linux 2023 Package Repository를 등록하고 `tailscaled`까지
  시작한다. Tailnet 인증은 Auth Key가 Terraform State나 EC2 User Data에 남지 않도록 Apply 후 SSM
  Session에서 별도로 수행한다.
- 신규 Develop은 NAT Gateway와 NAT Instance를 사용하지 않는다. Public IPv4와 Internet Gateway를
  통해 ECR, SSM, Tailscale, Grafana Cloud와 외부 AI API로 Outbound 통신한다.
- 신규 Develop은 Docker Compose로 FE, BE, MySQL을 실행하고 AI는 Runtime 계약이 확정된 뒤 추가한다.
- MySQL Develop Data는 합성 Fixture로 재생성 가능한 비영속 시험 Data로 취급한다.
- 신규 Develop은 Custom AMI나 EBS Snapshot에 의존하지 않고 AWS 공식 Amazon Linux 2023 AMI,
  Terraform, Bootstrap Script와 Docker Compose로 재생성한다.
- 서버가 중지돼도 CI가 Image를 ECR에 발행하고 FE·BE 희망 SHA를 Parameter Store에 보존한다.
  서버가 실행 중이면 전용 SSM Document로 즉시 재조정하고, 중지 상태라면 다음 부팅 시 systemd
  oneshot으로 최신 희망 조합을 반영한다.

## 구현 결과

- 신규 Develop EC2, SSM, Public Ingress 0개 Security Group과 Tailscale 연결 완료
- FE·BE `develop` Merge의 ECR Image 발행 완료
- Secrets Manager 기반 Secret Source of Truth와 배포 시점 임시 주입 적용 완료
- FE·Main Backend·Payment Server·MySQL 네 Container의 최초 배포와 Health 확인 완료
- Tailscale Serve의 Tailnet 전용 HTTPS와 Peter Browser 화면 확인 완료
- V1 Benchmark의 Route 53 A 레코드, 전용 인증서, Nginx Server Block과 Browser 화면 확인 완료
- FE·BE `develop` Merge 후 ECR 발행, 희망 SHA 갱신, SSM 재조정과 Browser 화면 자동 배포 검증 완료
- 팀원 초대 전 Tailnet Tag·Grant 최소 권한 적용 필요

## 문제

기존 `dev.seonjalal.com`은 팀 통합 환경으로 시작했지만 운영과 같은 V1 Compose 구조의 QA와 부하 시험
기준점으로 사용됐다. 이를 지속적인 `develop` Merge 자동 배포 대상으로 사용하면 V1과 V2의 동일
Image 비교 기준이 계속 바뀐다.

반대로 새 Develop까지 Private App Subnet에 배치하면 단일 서버를 위해 NAT Gateway, NAT Instance 또는
다수 Interface Endpoint가 필요하다. 신규 Develop의 목적은 고가용성 검증이 아니라 빠른 코드 통합과
사용자 확인이므로 그 운영비와 복잡도를 정당화하기 어렵다.

## 환경 역할

| 환경 | 역할 | 접근 | 배포·변경 정책 |
|---|---|---|---|
| 신규 Develop | `develop` Merge 통합과 기능 확인 | Tailscale 전용 | 자동 배포, 평상시 변경 허용 |
| V1 Benchmark | V1 기준 성능과 장애 양상 재현 | 시험 Window 전용 | Image Digest와 Fixture Version 고정 |
| 향후 V2 Staging | 운영 유사 QA·부하·Release 검증 | 별도 접근 계층 | 시험 중 배포 동결 |
| Production | 실제 사용자 서비스 | Public HTTPS | 수동 승인 배포 |

## Public Subnet 배치의 보안 경계

Public Subnet은 Internet Gateway로 향하는 Route가 있다는 뜻이며 Instance가 자동으로 외부에 공개된다는
뜻은 아니다. 신규 Develop은 다음 조건을 모두 유지한다.

- EC2 Security Group Ingress 규칙 0개
- SSH 22, HTTP 80, HTTPS 443과 Application Port를 Internet에 공개하지 않음
- Docker의 사용자 접근 Port는 Host Loopback에만 Bind
- Tailscale Serve가 Loopback Service를 Tailnet에만 제공
- EC2 Key Pair를 만들지 않고 SSM Session Manager로 관리
- IMDSv2만 허용
- Root EBS 암호화
- Instance API Termination Protection 활성화

Public IPv4는 Outbound 경로를 제공한다. Stop/Start로 Public IPv4가 바뀌어도 사용자는 Route 53 A Record가
아니라 Tailscale MagicDNS 이름으로 접근하므로 DNS 수동 변경이 필요 없다.

## V1 Benchmark 보존 정책

- Public Domain은 `v1-benchmark.seonjalal.com`을 사용한다.
- Elastic IP를 사용하지 않으므로 시험 시작 시 현재 Public IPv4와 Route 53 A 레코드의 일치 여부를 확인한다.
- HTTPS 인증서는 수동 DNS-01로 발급됐으며 2027-01-09 이전 갱신 또는 자동화 방식 전환이 필요하다.
- 시험 접근은 Security Group과 Host Nginx Allowlist의 승인된 IPv4 `/32`로 이중 제한한다.
- Termination Protection을 활성화했다.
- Root EBS의 `DeleteOnTermination`을 `false`로 변경했다.
- 현재 Root EBS는 암호화되지 않았고 Snapshot과 Private AMI는 없다.
- 기존 Test Data는 부족하며 확장·재생성이 필요하므로 현 상태의 AMI와 Snapshot을 만들지 않는다.
- V1과 V2 비교는 Disk Snapshot이 아니라 FE·BE Image Digest, Migration Version, Fixture Version,
  k6 Scenario와 관측 조건을 고정해 재현한다.
- `DeleteOnTermination=false`는 Backup이 아니다. Instance가 의도치 않게 종료됐을 때 Volume 자동 삭제를
  막는 마지막 안전장치다.

## 제외한 대안

| 대안 | 제외 이유 |
|---|---|
| 기존 V1 서버를 지속적 Develop로 계속 사용 | Benchmark 기준 Image와 Data가 계속 바뀐다. |
| 신규 Develop을 Private App Subnet에 배치 | 단일 비고가용 서버를 위해 NAT 또는 Endpoint 운영비가 추가된다. |
| 신규 Develop에 Elastic IP와 Public Route 53 Record 사용 | Stop 중에도 고정 Public IPv4 비용과 DNS 운영이 필요하고 VPN 전용 경계가 흐려진다. |
| 실행 중인 기존 서버를 Custom AMI로 캡처 | 부족한 Test Data, Secret, Log와 Docker Cache까지 Root Snapshot에 포함된다. |
| Develop MySQL을 RDS로 분리 | 빠른 통합 환경의 비용과 관리 범위를 늘리며 Data는 합성 Fixture로 재생성 가능하다. |

## 재검토 조건

- Develop에서 운영 유사 부하 시험이나 고가용성 검증이 필요해짐
- 여러 EC2 또는 ECS Task가 생겨 Public IPv4 방식의 관리 범위가 커짐
- 보안 정책이 모든 Workload의 Private Subnet 배치를 요구함
- Develop Data가 재생성 불가능하거나 팀의 중요 업무 Data가 됨
- AI Runtime을 포함한 실제 CPU·Memory 요구가 단일 EC2 용량을 초과함

## 공식 근거

- [EC2 Stop과 Start 동작](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/how-ec2-instance-stop-start-works.html)
- [SSM Agent가 사전 설치된 AMI](https://docs.aws.amazon.com/systems-manager/latest/userguide/ami-preinstalled-agent.html)
- [Tailscale Serve](https://tailscale.com/docs/features/tailscale-serve)
- [Tailscale HTTPS](https://tailscale.com/docs/how-to/set-up-https-certificates)
- [Tailscale Linux 설치](https://tailscale.com/docs/install/linux)
- [Tailscale Amazon Linux 2023 Package](https://pkgs.tailscale.com/stable/)
- [Terraform State](https://developer.hashicorp.com/terraform/language/state)
- [Terraform S3 Backend](https://developer.hashicorp.com/terraform/language/backend/s3)
