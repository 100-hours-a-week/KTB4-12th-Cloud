# ADR-002: V2 Private Network Egress 전략

- 상태: 재검토 중
- 결정일: 2026-10-06
- 재검토일: 2026-10-09
- 범위: V2 Develop·Production VPC의 외부 및 AWS Service 접근 경로

## 2026-10-09 재검토 경계

- Foundation은 VPC, Public/App/Data Subnet, Internet Gateway, 계층별 Route Table, S3 Gateway
  Endpoint와 Security Group까지만 구성한다.
- NAT Gateway, NAT Instance, App Subnet의 Internet Default Route는 Foundation Apply 범위에서 제외한다.
- AI Runtime의 외부 API 호출, ECS/ECR/Logs/Secrets/SSM Endpoint 수와 비용을 확인한 뒤 Egress Root 또는
  Module로 분리한다.
- 아래 결정은 2026-10-06 기준안이며 재검토가 끝날 때까지 Apply 근거로 사용하지 않는다.

## 결정

| 환경 | NAT Gateway | Gateway Endpoint | Interface Endpoint 초기값 |
|---|---:|---|---|
| Develop | Public Subnet 한 AZ에 1개 | S3 | 없음 |
| Production | AZ별 1개, 총 2개 | S3 | Traffic·보안·비용 측정 후 선택 |

- ALB만 Public Subnet에 배치한다.
- ECS Container Instance와 Task는 Private App Subnet에 두고 Public IP를 할당하지 않는다.
- RDS는 Private Data Subnet에 두며 Internet Route를 만들지 않는다.
- Develop의 두 App Subnet은 초기에는 NAT Gateway 한 개를 공유한다.
- Production은 AZ별 NAT Gateway와 AZ별 Route Table을 사용해 한 AZ의 NAT 장애가 다른 AZ의 Egress로
  전파되지 않게 한다.
- S3 Gateway Endpoint를 기본 적용해 ECR Image Layer와 S3 Traffic이 NAT를 통과하지 않게 한다.
- 외부 AI API 접근이 필요하므로 Interface Endpoint만으로 NAT를 완전히 제거하지 않는다.
- ECR API/DKR, CloudWatch Logs, Secrets Manager Interface Endpoint는 실제 NAT 처리량과 Endpoint
  시간 비용을 비교한 뒤 선택적으로 추가한다.

## 왜 Egress가 필요한가

Private ECS Host와 Task는 다음 대상에 접근해야 한다.

- ECR API와 Docker Registry
- ECR Image Layer가 저장된 S3
- CloudWatch Logs와 Metric
- Secrets Manager 또는 SSM Parameter Store
- OS Package·ECS Agent Update 경로
- 외부 AI API와 향후 외부 Service API

Private Subnet은 Internet Gateway로 직접 나갈 수 없다. NAT Gateway 또는 대상 AWS Service의 VPC
Endpoint가 없으면 ECS Image Pull, Log 전송, Secret 조회나 외부 API 호출이 실패할 수 있다.

## 대안 비교

| 대안 | 비용 특성 | 가용성·운영 | 판단 |
|---|---|---|---|
| Public IP Task/Host | NAT 비용 없음 | Public 노출 경계 증가, Task IP 변화 | 제외 |
| NAT Instance | EC2 실행·전송 비용 | Patch, Scale, Source/Destination Check, 장애 전환 직접 운영 | 비선택 |
| Develop NAT Gateway 2개 | AZ 독립 | V2 초기·시험 환경에 상시 비용이 큼 | 초기 비선택 |
| Develop NAT Gateway 1개 | NAT 시간·처리 비용 | 한 AZ Egress 장애 허용, Cross-AZ Traffic 가능 | 선택 |
| Production NAT Gateway 2개 | 가장 높은 상시 비용 | AZ별 독립 Egress, 운영 부담 낮음 | 선택 |
| Interface Endpoint만 사용 | Endpoint별·AZ별 시간 비용 | AWS Service는 Private 접근, 외부 AI API 접근 불가 | 단독 사용 제외 |
| NAT + 선택 Endpoint | 중복 시간 비용 가능 | AWS Service Traffic과 외부 Internet 경로 분리 | 측정 후 확대 |

NAT Instance는 작은 Traffic에서 저렴할 수 있지만 Instance Patch, 장애 감지, Route 전환과 처리량을
직접 책임져야 한다. ECS on EC2 Host 운영에 추가로 Egress Appliance까지 운영하면 1인 Cloud 담당의
장애 범위가 커져 초기안에서 제외한다.

## Develop의 허용 범위

Develop NAT Gateway 한 개는 비용을 줄이기 위한 의도적인 가용성 절충이다.

- NAT가 있는 AZ 장애 시 다른 AZ Task도 외부 AI API와 일부 AWS API 접근을 잃을 수 있다.
- 이미 실행 중인 Task의 ALB 내부 요청과 RDS 연결은 해당 Component가 정상이라면 계속 가능하다.
- Image Pull, Scale-out, Secret 재조회와 외부 API 호출은 실패할 수 있다.
- 이 구조를 Production의 Multi-AZ 가용성 증거로 사용하지 않는다.

Develop의 목적은 기능 통합, 배포·Rollback, 부하·장애 시험이다. NAT 장애 허용 범위도 Drill로
기록하고 Production에서는 AZ별 NAT로 보완한다.

## Route 원칙

```text
Public Subnet A/C
  0.0.0.0/0 → Internet Gateway

Develop Private App Subnet A/C
  0.0.0.0/0 → NAT Gateway A
  S3 Prefix List → S3 Gateway Endpoint

Production Private App Subnet A
  0.0.0.0/0 → NAT Gateway A

Production Private App Subnet C
  0.0.0.0/0 → NAT Gateway C

Private Data Subnet A/C
  Internet Default Route 없음
```

RDS가 AWS API 또는 Internet에 직접 나가야 하는 요구는 현재 없다. Data Subnet에 NAT Route를 넣지
않는다.

## 비용 판정 방식

정확한 비용은 Apply 직전에 서울 Region의 공식 Pricing Calculator로 다시 계산한다.

입력 항목:

- NAT Gateway 개수와 월 실행시간
- NAT 처리 Data GB
- Cross-AZ Data GB
- Interface Endpoint 개수 × AZ 수 × 실행시간
- Endpoint 처리 Data GB
- S3 Gateway Endpoint 비용
- CloudWatch Log 수집·보존·조회량

단순히 NAT 시간 비용만 비교하지 않는다. ECR Image Pull, Log, 외부 AI API Traffic과 Cross-AZ
전송을 함께 넣는다. Develop은 시험하지 않는 시간에 VPC 전체를 자동 삭제하는 방식 대신, 먼저
NAT의 실제 비용과 재구축 시간을 측정한 뒤 Schedule 또는 환경 수명주기를 정한다.

## 보안 경계

- NAT Gateway는 외부에서 Private Instance로 새 Connection을 시작하게 하지 않는다.
- Security Group은 ALB→ECS, ECS→RDS처럼 Source Security Group으로 제한한다.
- Network ACL은 Stateless 특성 때문에 초기에는 기본 허용을 유지하고 Security Group을 주 통제로
  사용한다.
- VPC Flow Logs는 Reject 분석과 Egress 목적지 확인에 사용하되 보존기간과 비용을 제한한다.
- Secret과 Token을 User Data, Terraform State 입력 또는 Log에 기록하지 않는다.

## 구현·검증 순서

1. VPC, Internet Gateway와 Public/App/Data Subnet
2. Public Route Table과 Develop NAT Gateway 1개
3. App Route Table과 S3 Gateway Endpoint
4. Data Route Table에 Internet Default Route가 없는지 확인
5. VPC Flow Logs의 목적지·보존 방식 결정
6. Private EC2 Probe에서 ECR/S3/Logs/Secrets/외부 HTTPS 접근 검증
7. NAT 장애와 ECS Scale-out 실패 양상 기록
8. Production 전 공식 비용 계산과 NAT 2개 Plan 검토

## 재검토 조건

- 외부 AI API가 PrivateLink 또는 VPC 내부 Service로 바뀌어 Internet Egress가 불필요해짐
- NAT 처리·Cross-AZ 비용이 Interface Endpoint 조합보다 지속적으로 높음
- Develop에서도 NAT AZ 장애 중 Scale-out과 배포를 유지해야 하는 요구가 생김
- 보안 정책이 Internet Egress 목적지 Allowlist 또는 Inspection Appliance를 요구함
- 월 30만 원 예산에서 Production NAT 2개가 다른 필수 가용성 비용과 함께 상한을 초과함

## 공식 근거

- [Amazon VPC Pricing](https://aws.amazon.com/vpc/pricing/)
- [AWS PrivateLink Pricing](https://aws.amazon.com/privatelink/pricing/)
- [NAT Gateway 기본 사항](https://docs.aws.amazon.com/vpc/latest/userguide/nat-gateway-basics.html)
- [ECR VPC Endpoint](https://docs.aws.amazon.com/AmazonECR/latest/userguide/vpc-endpoints.html)
- [Gateway Endpoint](https://docs.aws.amazon.com/vpc/latest/privatelink/gateway-endpoints.html)
