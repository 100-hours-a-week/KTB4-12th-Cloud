# V1 AWS 장애 알림 → Discord 구축 가이드

## 현재 운영 상태

- 기준일: 2026-10-07
- Production CloudWatch Alarm 3개의 `ALARM`·`OK` Action이 활성화돼 있다.
- SNS → Lambda → Secrets Manager → Discord 수동 발행과 실제 `ALARM`·`OK` 상태 전환을 검증했다.
- Terraform State는 S3 Key `seonjalal/production/alerting/terraform.tfstate`에서 관리한다.
- 상세 실행·검증·트러블슈팅 기록은
  `docs/2026-10-07-v1-alerting-activation-and-state-migration.md`를 따른다.

## 1. 이번 구현 범위

첫 단계는 CloudWatch 기본 EC2 Metric만 사용해 다음 경보를 Discord로 전송한다.

| Alarm | 초기 조건 | 복구 알림 | 의미 |
|---|---:|---:|---|
| System Status Check | 2분 연속 실패 | 전송 | AWS 물리 호스트·전원·네트워크 계층 문제 |
| Instance Status Check | 3분 연속 실패 | 전송 | Guest OS·인스턴스 네트워크 문제 |
| CPU High | 평균 80% 이상 10분 | 전송 | 지속 CPU 포화 후보 |

CPU 80%는 확정 SLO가 아닌 초기 운영값이다. 실제 Baseline과 오탐을 확인한 후 조정한다.
메모리·디스크는 EC2 기본 Metric이 아니므로 CloudWatch Agent를 설치하는 두 번째 단계에서 추가한다.

```text
CloudWatch Alarm
       │ ALARM / OK
       ▼
    SNS Topic
       ▼
Lambda Formatter ── Secrets Manager에서 Webhook 조회
       │ HTTPS
       ▼
Discord infra_error
```

Lambda는 VPC에 연결하지 않는다. Discord로 나가는 인터넷 통신만 필요하며, VPC private subnet에
넣으면 NAT Gateway 또는 별도 인터넷 송신 구성이 필요해 비용과 장애 지점이 늘어난다.

## 2. 실제 변경 전 확인할 값

- AWS 계정 ID
- 리전: 현재 계약은 `ap-northeast-2`
- Production 또는 Develop EC2 Instance ID
- Discord에서 채널·Webhook을 만들 권한
- Terraform State Backend와 Key. 현재 Production Alerting은 S3 Remote Backend를 사용한다.

대상 AWS 계정과 환경이 확인되기 전에는 `terraform apply`를 실행하지 않는다.

### Terraform State와 Discord 기록은 서로 다르다

Terraform State는 장애 알림이나 운영 로그를 저장하는 파일이 아니다. Terraform 코드의 리소스와
AWS의 실제 리소스 ID를 연결하는 관리 데이터다. 예를 들어 Terraform은 State를 통해
`aws_sns_topic.infrastructure_alerts`가 AWS의 어느 SNS Topic ARN인지 구분한다.

초기 구축 때 사용한 Local State는 2026-10-07에 S3 Backend로 이전했다. 이후 `plan`, `apply`,
`import`와 `state` 명령은 S3의 최신 State를 자동으로 읽고 쓰며 S3 Native Lock File로 동시 실행을
제어한다. 매 작업마다 Local State를 S3에 수동으로 복사하거나 업로드하지 않는다.

Migration 전 Local State와 Backup은 Remote State 무변경 Plan 검증 후에도 보존 정책이 정해질 때까지
권한 `600`으로 단기 보존한다. 이 파일은 현재 Backend의 기준 State가 아니며 Git에 Commit하지 않는다.

S3 버킷이 기술적으로 반드시 별도여야 하는 것은 아니지만, 상품 이미지 Bucket은 애플리케이션 Role,
Lifecycle과 삭제 범위가 State와 다르므로 재사용하지 않는다. 이 프로젝트에서는 계정별 State Bucket
하나를 만들고 `production/alerting`, `production/network`, `develop/network`처럼 Key Prefix로 나눠
재사용한다. Alert 하나마다 Bucket을 만드는 구조가 아니다.

State에는 향후 민감한 Resource Attribute가 포함될 수 있으므로 Git에 Commit하지 않고, S3 Versioning,
Public Access Block, 암호화와 State Lock을 사용한다.

## 3. Discord 채널과 Webhook 준비

실행 위치: Discord Desktop 또는 Web.

1. 현재 `채팅 채널` 제목 오른쪽의 `+`를 선택한다.
2. 채널 유형은 `텍스트`, 채널 이름은 기존 `be_error`, `fe_error` 규칙에 맞춰 `infra_error`로 지정한다.
3. 팀 전체가 장애를 확인해야 하므로 우선 공개 채널로 만들고, 생성 후 `채널 편집 → 권한`에서
   `@everyone`의 `채널 보기`는 허용하되 `메시지 보내기`는 비활성화하는 것을 권장한다.
4. Cloud 담당자·관리자 역할에는 장애 대응 기록을 남길 수 있도록 `메시지 보내기`를 허용한다.
5. 서버 설정 → `연동`(`Integrations`) → `웹후크`(`Webhooks`) → `새 웹후크`를 선택한다.
6. 이름을 `Seonjalal AWS Infra Alert`로 지정하고 채널은 `infra_error`를 선택한다.
7. `웹후크 URL 복사`를 선택한다.

Webhook URL은 해당 채널에 메시지를 쓸 수 있는 Secret이다. 문서, GitHub Issue, Shell History,
Terraform 변수와 State에 기록하지 않는다. 노출됐다면 Discord에서 즉시 Webhook을 삭제하고 새로 만든다.

## 4. Secrets Manager에 Webhook 저장

실행 위치: AWS Console, 대상 환경과 같은 AWS 계정·리전.

1. Secrets Manager → `Store a new secret`
2. `Other type of secret`
3. Key/value 선택
4. Key: `webhook_url`
5. Value: Discord에서 복사한 URL
6. Secret 이름: `seonjalal/production/discord-infra-alert`
7. 설명: `Discord webhook for production infrastructure alerts`
8. 자동 회전은 사용하지 않음
9. 생성 후 Secret ARN만 기록

Develop도 알림을 운영한다면 `seonjalal/develop/discord-infra-alert`로 분리한다. 같은 Webhook을
공유하면 환경 오인이 발생하고 한 번의 유출이 두 환경에 영향을 준다.

## 5. 로컬 정적 검증

아래 명령은 AWS 리소스를 변경하지 않는다.

실행 위치: Mac, `KTB4-12th-Cloud/operations/alerting/terraform`.

```bash
# Git에서 제외된 backend.hcl로 S3 Backend와 Provider를 초기화합니다.
# AWS 리소스는 변경하지 않지만 S3 State와 Lock에 접근합니다.
AWS_PROFILE=seonjalal-production terraform init \
  -backend-config=backend.hcl

# Terraform 파일 형식을 확인하고 자동 정렬합니다.
terraform fmt -check -recursive

# 변수, Provider와 리소스 참조의 정합성을 검사합니다.
terraform validate
```

Lambda 단위 테스트는 Discord나 AWS에 접속하지 않는다.

실행 위치: Mac, `KTB4-12th-Cloud/operations/alerting/lambda`.

```bash
# 외부 호출을 Mock 처리한 메시지 변환·전달 단위 테스트를 실행합니다.
python3 -m unittest -v test_handler.py
```

## 6. Terraform 입력 파일 준비

실행 위치: Mac, `operations/alerting/terraform`.

```bash
# 실제 환경 입력 파일을 예시에서 복사합니다.
# 이 파일은 Git에 Commit하지 않습니다.
cp terraform.tfvars.example terraform.tfvars

# 소유자만 읽고 쓸 수 있도록 권한을 제한합니다.
chmod 600 terraform.tfvars
```

다음 값만 실제 환경 값으로 바꾼다.

- `aws_account_id`
- `environment`
- `ec2_instance_id`
- `discord_webhook_secret_arn`

첫 적용에서는 `alarm_actions_enabled = false`를 유지한다. 이 상태에서는 Alarm이 생성돼도 Discord
전송 Action이 연결되지 않아, 연결 시험 전 실제 장애 알림이 우발적으로 전송되는 일을 방지한다.

## 7. 계정 확인과 Plan

다음 단계부터 AWS API를 읽는다. `apply` 전까지 리소스는 변경하지 않는다.

실행 위치: Mac, AWS CLI에 대상 계정용 자격 증명이 설정된 Shell.

```bash
# Production Profile의 Account와 ARN을 확인해 IAM 사용자인지 검증합니다.
aws sts get-caller-identity \
  --profile seonjalal-production

# 대상 EC2가 입력한 리전과 계정에 존재하는지 읽기 전용으로 확인합니다.
aws ec2 describe-instances \
  --instance-ids '<대상-instance-id>' \
  --region ap-northeast-2 \
  --query 'Reservations[0].Instances[0].[InstanceId,State.Name,Tags]' \
  --output table

# S3 Backend와 Provider를 초기화합니다.
AWS_PROFILE=seonjalal-production terraform init \
  -backend-config=backend.hcl

# 생성·변경될 리소스를 파일로 고정합니다. 아직 AWS 리소스는 변경하지 않습니다.
AWS_PROFILE=seonjalal-production terraform plan \
  -out=alerting.tfplan

# Plan에서 예상하지 않은 삭제·교체와 다른 환경 리소스가 없는지 사람이 검토합니다.
terraform show alerting.tfplan
```

예상 생성 대상은 SNS Topic 1개, Lambda 1개, IAM Role/Policy, Log Group, SNS Subscription,
CloudWatch Alarm 3개다. 기존 EC2와 Compose를 재시작하거나 변경하지 않는다.

현재 기준 State는 S3의 `seonjalal/production/alerting/terraform.tfstate`다. `backend.hcl`, 실제
`terraform.tfvars`, Plan과 State 사본은 Git Ignore 대상이다. Backend를 다시 이전해야 한다면 기존
State와 새 목적지를 읽기 전용으로 확인하고 Backup을 만든 뒤 `init -migrate-state`를 사용한다.

## 8. 첫 적용과 연결 시험

이 단계는 AWS 리소스를 생성하고 소액의 사용량 기반 비용이 발생한다. 계정·환경·Plan을 확인하고
명시적으로 승인한 뒤에만 실행한다.

```bash
# 검토한 Plan 그대로 첫 단계 리소스를 생성합니다.
terraform apply alerting.tfplan
```

첫 적용은 Alarm Action이 꺼져 있다. AWS Console에서 다음을 확인한다.

1. Lambda가 VPC에 연결되지 않았는지 확인
2. Lambda Environment에 Webhook URL이 아니라 Secret ARN만 있는지 확인
3. Lambda Role이 지정한 Secret 하나의 `GetSecretValue`만 허용하는지 확인
4. CloudWatch Log 보존기간이 30일인지 확인
5. Alarm 세 개의 Instance ID가 정확한지 확인

SNS Console에서 생성된 Topic을 선택하고 `Publish message`로 `manual alert path test`를 보낸다.
Discord에 파란색 `[INFO]` 메시지가 도착하면 SNS → Lambda → Secret → Discord 경로가 정상이다.

실패했다면 Webhook을 출력하지 말고 다음만 확인한다.

- Lambda `Invocations`, `Errors`, `Throttles`
- `/aws/lambda/<함수명>` Log Group의 오류 종류
- Secret ARN과 리전
- Lambda Role의 Secret 권한
- Discord에서 Webhook이 삭제되지 않았는지

## 9. Alarm Action 활성화

SNS 수동 시험이 성공한 뒤 `terraform.tfvars`를 다음과 같이 바꾼다.

```hcl
alarm_actions_enabled = true
```

실행 위치: Mac, `operations/alerting/terraform`.

```bash
# Alarm과 SNS 연결만 추가되는지 새 Plan을 확인합니다.
AWS_PROFILE=seonjalal-production terraform plan \
  -out=enable-alert-actions.tfplan

# 저장한 Plan에서 세 Alarm의 alarm_actions와 ok_actions만 바뀌는지 확인합니다.
terraform show enable-alert-actions.tfplan

# 승인 후 검토한 Plan으로 ALARM·OK 알림을 활성화합니다.
AWS_PROFILE=seonjalal-production terraform apply \
  enable-alert-actions.tfplan
```

운영 인스턴스를 실제로 중지하거나 CPU 부하를 발생시키지 않는다. CloudWatch Console의 Alarm 시험
또는 `SetAlarmState` 기능을 사용해 한 Alarm을 임시 `ALARM`으로 바꾸고 Discord 수신을 확인한다.
시험 메시지에는 `TEST`임을 명확히 기록하고, 시험 후 `OK`로 되돌린다. Metric이 다음 평가에서 실제
상태로 다시 바뀔 수 있다.

2026-10-07 검증에서는 CPU High Alarm을 `SetAlarmState`로 `ALARM` 전환한 뒤 Discord 수신을 확인했고,
다음 실제 Metric 평가에서 약 1분 뒤 `OK`로 자동 복구돼 복구 알림까지 확인했다. 운영 Instance를
중지하거나 CPU 부하를 발생시키지 않았다.

## 10. 긴급 중지와 복구

오탐이 반복되거나 Discord가 과도하게 울리면 리소스를 삭제하지 말고 Action부터 끈다.

실행 위치: Mac, AWS CLI에 대상 계정용 자격 증명이 설정된 Shell.

```bash
# 지정한 Alarm의 Action만 중지하며 Metric 평가와 History는 보존합니다.
aws cloudwatch disable-alarm-actions \
  --alarm-names \
    'seonjalal-production-ec2-system-status-failed' \
    'seonjalal-production-ec2-instance-status-failed' \
    'seonjalal-production-ec2-cpu-high' \
  --region ap-northeast-2
```

원인을 수정한 뒤 Terraform의 `alarm_actions_enabled` 값과 실제 상태를 일치시키고 다시 적용한다.
Webhook이 유출된 경우 Discord Webhook 삭제 → 새 Webhook 생성 → Secrets Manager에 새 Version 저장
순으로 회전한다. Lambda 코드는 변경할 필요가 없다.

## 11. 2단계 확장

기본 경로가 안정화된 후 다음 순서로 확장한다.

1. CloudWatch Agent를 SSM으로 설치해 Memory와 Root Disk Metric 수집
2. Memory 85% 지속, Disk 85% 경고 추가
3. 외부 HTTPS Synthetic Check 추가
4. Docker unhealthy/restart를 Custom Metric으로 발행
5. Backend 5xx 비율과 응답시간 경보 추가
6. Lambda 자체 `Errors > 0`과 SNS 전달 실패 경보 추가
7. Develop/Production Topic·Secret·Discord Channel 완전 분리

## 공식 근거

- [Discord Webhook 소개](https://support.discord.com/hc/en-us/articles/228383668-Intro-to-Webhooks)
- [CloudWatch Alarm의 EC2 Action과 Status Check](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/UsingAlarmActions.html)
- [EC2 Status Check 종류](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/monitoring-system-instance-status-check.html)
- [SNS에서 Lambda 호출](https://docs.aws.amazon.com/sns/latest/dg/sns-lambda-as-subscriber.html)
- [Secrets Manager 개요](https://docs.aws.amazon.com/secretsmanager/latest/userguide/intro.html)
- [CloudWatch Agent 설치](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/Install-CloudWatch-Agent.html)
- [Terraform S3 Backend](https://developer.hashicorp.com/terraform/language/backend/s3)
