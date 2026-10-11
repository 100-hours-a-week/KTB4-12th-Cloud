# V1 장애 알림 활성화와 Terraform State Migration 작업 기록

- 작업일: 2026-10-07
- 시간대: Asia/Seoul
- 담당: Cloud Peter
- 환경: V1 Production
- 실행 위치: Mac Local `KTB4-12th-Cloud/operations/alerting/terraform`
- AWS Region: `ap-northeast-2`
- 결과: Alarm Action 활성화, Discord `ALARM`·`OK` 검증, S3 Remote State 이전 완료

## 결론

Production CloudWatch Alarm 세 개의 `ALARM`·`OK` Action을 기존 SNS Topic에 연결했다. 운영 EC2를
중지하거나 CPU 부하를 발생시키지 않고 CPU High Alarm의 상태만 `TEST`로 전환해 Discord
`infra_error` 채널에서 `ALARM`과 실제 Metric 평가에 따른 `OK` 복구 메시지를 확인했다.

Alerting Terraform의 Local State는 공용 S3 State Bucket의
`seonjalal/production/alerting/terraform.tfstate`로 이전했다. Remote State에서 기존 12개 주소를
동일하게 읽었고 최종 Plan은 `No changes`였다. 이후 State는 Terraform이 S3에서 자동으로 읽고 쓰며,
매 변경마다 Local State를 수동 업로드하지 않는다.

## 5W1H

| 구분 | 내용 |
|---|---|
| What | CloudWatch Alarm 3개의 ALARM·OK Action 활성화, Discord 실제 상태 전환 시험, Alerting State의 S3 Backend 이전 |
| When | 2026-10-07. Discord ALARM 17:13 KST, 자동 OK 17:14 KST, Remote State Object 생성 17:37 KST |
| Where | Mac Local의 Alerting Terraform Root, Production AWS 서울 Region, Discord `infra_error` |
| Who | Cloud 담당 Peter가 명령을 직접 실행하고 Codex가 Plan·중단 기준·검증 절차를 단계별 안내 |
| Why | 수동 SNS 시험만 통과한 비활성 Alarm을 실제 운영 경보로 전환하고, 단일 Mac의 Local State 의존성을 제거하기 위해 수행 |
| How | 계정·IAM 주체 확인 → 설정 변경 → 정적 검사 → 저장 Plan 검토 → 승인·Apply → ALARM/OK 시험 → State Backup → S3 Migration → Object·State·무변경 Plan 검증 |

## 변경 전 상태

| 항목 | 상태 |
|---|---|
| SNS → Lambda → Discord | SNS 수동 발행으로 연결 경로만 확인 |
| CloudWatch Alarm | 3개 존재, `alarm_actions`와 `ok_actions` 비활성 |
| Terraform Backend | Local `terraform.tfstate` |
| Remote 목적지 | State Bucket은 구축됐으나 Alerting Prefix는 비어 있음 |
| 운영 공백 | 실제 Alarm 상태 전환이 Discord로 전달되지 않음 |

## 작업 범위와 제외 범위

### 포함

- System Status Check, Instance Status Check, CPU High Alarm의 ALARM·OK Action
- 기존 SNS Topic, Lambda Formatter, Secrets Manager Webhook과 Discord 경로
- Alerting Terraform State의 S3 Backend Migration
- Local State Backup, S3 Object Metadata, Lock과 무변경 Plan 검증

### 제외

- EC2 중지·재시작과 CPU 부하 발생
- Alarm 임계값 변경
- Memory·Disk·외부 HTTPS·Container Health Alarm 추가
- Lambda, SNS, IAM, Secret과 EC2의 생성·삭제·교체
- Webhook Value, Access Key, Token과 State 원문 조회

## 실행 과정

### 1. Discord 수동 경로 확인

기존 SNS 수동 발행 메시지가 Discord `infra_error` 채널에 `[INFO]`로 도착한 것을 확인했다. 이 결과는
SNS Topic, Lambda Invoke Permission, Lambda Formatter, Secret 조회와 Discord Webhook 경로가
정상이라는 증거로 사용했다. 다만 CloudWatch Alarm Action은 비활성 상태였으므로 운영 경보가 자동으로
전송되는 상태는 아니었다.

### 2. AWS Account와 IAM 주체 확인

Apply 전에 `aws sts get-caller-identity`를 실행했다. 기본 Profile은 대상 Production 계정이 아닌 다른
계정을 가리켰으므로 작업을 중단했다. Local Profile 목록에서 `seonjalal-production`을 확인하고 해당
Profile을 대상으로 다시 인증했다.

최종 Apply와 Migration은 Production 계정의 전용 IAM 사용자로 수행했다. Account ID와
사용자 ARN은 공개 Issue·PR에서 마스킹한다.

### 3. Alarm Action Plan과 Apply

`terraform.tfvars`의 환경이 `production`, `alarm_actions_enabled`가 `false`인 것을 Secret을 출력하지
않고 확인한 뒤 값을 `true`로 변경했다. `terraform fmt -check -recursive`와 `terraform validate`를
통과한 후 Production Profile로 저장 Plan을 생성했다.

| Plan 항목 | 결과 |
|---|---:|
| Add | 0 |
| Change | 3 |
| Destroy | 0 |

변경 대상은 세 `aws_cloudwatch_metric_alarm`의 `alarm_actions`와 `ok_actions`뿐이었다. 명시적 승인 후
저장 Plan을 적용했고 결과는 `0 added / 3 changed / 0 destroyed`였다. EC2, Application, Lambda, SNS,
IAM과 Secret에는 변경이 없었다.

### 4. ALARM과 자동 복구 검증

세 Alarm 모두 다음 상태임을 읽기 전용으로 확인했다.

- `ActionsEnabled=True`
- `AlarmActionCount=1`
- `OKActionCount=1`
- 시험 전 상태 `OK`

운영 Instance에 장애나 부하를 만들지 않고 CPU High Alarm에 `TEST` 사유로 `SetAlarmState`를 실행했다.
17:13 KST에 Discord `[ALARM]` 메시지가 도착했다. 다음 실제 CPU Metric 평가에서 임계값 미만임이
확인돼 17:14 KST에 `[OK]` 메시지가 자동으로 도착했다. 최종 Alarm 상태는 `OK`, Action은 활성 상태로
유지됐다.

### 5. Local State Backup과 Remote 목적지 확인

Migration 전 `terraform state list`에서 Data Source 1개와 Managed Resource 11개, 총 12개 주소를
확인했다. Alerting S3 Prefix가 비어 있음을 읽기 전용으로 확인해 기존 State 덮어쓰기 위험을
제거했다.

Local State를 `terraform.tfstate.pre-s3-migration-20261007`로 복사하고 원본과 Backup 권한을 `600`으로
제한했다. 두 파일의 SHA-256이 일치하는 것을 확인했다. State 내용과 Hash 원문은 외부 기록에 넣지
않는다.

### 6. S3 Backend Migration

`versions.tf`에 빈 S3 Backend 선언을 추가했다. 실제 Bucket, Key와 Local Profile은 Git에서 제외되는
`backend.hcl`로 분리했다. 다음 명령으로 Local State를 S3로 복사했다.

```bash
# 기존 Local State를 검토한 S3 Backend로 한 번만 이전한다.
AWS_PROFILE=seonjalal-production terraform init \
  -migrate-state \
  -backend-config=backend.hcl
```

Terraform Prompt에서 기존 Local State가 있고 새 S3 Backend에는 State가 없다는 내용을 확인한 뒤
복사를 승인했다. `Successfully configured the backend "s3"`와 초기화 성공 메시지를 확인했다.

Migration은 Backend를 바꾸는 일회성 작업이다. 이후 `plan`, `apply`, `import`와 `state` 명령은 S3
State를 자동으로 사용한다. State 파일을 매번 수동으로 S3에 올리지 않는다.

### 7. Remote State 검증

| 검증 항목 | 결과 |
|---|---|
| S3 Key | `seonjalal/production/alerting/terraform.tfstate` |
| Content Type | `application/json` |
| Encryption | `AES256` |
| Size | 29,236 Byte |
| Versioning | Version ID 존재 |
| 활성 Lock | `.tflock` 없음 |
| State 주소 | Migration 전과 같은 12개 |
| 최종 Plan | `No changes` |

State Bucket의 Versioning, Public Access Block, TLS 강제와 삭제 방지는 기존 Bootstrap 검증 결과를
따른다. Migration이 끝난 후 Alerting Prefix에는 State Object만 남고 활성 Lock은 정상 해제됐다.

## 트러블슈팅

### T1. 기본 AWS Profile이 다른 계정을 가리킴

#### 현상과 영향

최초 `get-caller-identity` 결과가 Discord 수동 메시지와 Terraform이 대상으로 하는 Production 계정과
달랐다. 그대로 진행하면 잘못된 계정에서 조회·Plan을 수행하거나 Provider의 Account Guard에 의해
작업이 실패할 수 있었다.

#### 원인

명령에 `--profile` 또는 `AWS_PROFILE`을 지정하지 않아 Local AWS CLI의 `default` Profile이 사용됐다.

#### 해결과 검증

- 즉시 작업을 중단하고 Local Profile 이름만 조회했다.
- `seonjalal-production` Profile을 명시해 다시 인증했다.
- `get-caller-identity`에서 대상 Account와 IAM 사용자 ARN을 확인한 뒤에만 Plan을 실행했다.
- 이후 모든 AWS CLI와 Terraform 명령에 Production Profile을 명시했다.

#### 재발 방지

- Apply·Migration 전 Account, ARN, Region과 Environment를 다시 확인한다.
- Production 명령 예시에는 `AWS_PROFILE=seonjalal-production` 또는 `--profile`을 생략하지 않는다.
- Terraform Provider의 `allowed_account_ids`를 유지한다.

### T2. 만료 Session 재인증 중 root 자격 증명으로 Profile이 덮어써짐

#### 현상과 영향

`seonjalal-production` Session이 만료돼 `aws login`을 실행했으나 브라우저가 root Console Session을
사용했다. CLI가 기존 IAM 사용자 Session을 root Session으로 덮어쓸지 물었고 승인 후 Profile의 ARN이
root로 바뀌었다.

#### 조사와 배제

- `get-caller-identity`로 대상 Account는 맞지만 인증 주체가 root임을 확인했다.
- Root Session에서는 신원 조회 외에 Plan, Apply, State Migration과 Resource 변경을 실행하지 않았다.
- Terraform 코드나 State 손상 문제가 아니라 Local 인증 주체 선택 문제로 한정했다.

#### 해결과 검증

- `aws logout --profile seonjalal-production`으로 Local Login Cache를 제거했다.
- 브라우저의 root Console Session에서도 로그아웃했다.
- Production 전용 IAM 사용자로 Console에 로그인한 뒤 `aws login`을 다시 실행했다.
- 덮어쓰기 Prompt의 새 ARN이 IAM 사용자임을 확인한 뒤 승인했다.
- 최종 `get-caller-identity`에서 IAM 사용자 ARN을 재확인했다.

#### 재발 방지

- `aws login` 전 브라우저의 현재 AWS Console 인증 주체를 확인한다.
- Prompt에 root가 표시되면 `no`로 중단한다.
- Root는 계정 복구 등 root 전용 작업 외에는 사용하지 않는다.
- 가능하면 장기적으로 IAM Identity Center 또는 제한된 AssumeRole 기반 Local 접근으로 전환한다.

### T3. Local Shell에 `rg`가 없음

설정의 두 항목만 안전하게 확인하려 했으나 Local Shell에 `rg`가 설치돼 있지 않아 실행이 실패했다.
Secret이 포함된 전체 `terraform.tfvars`를 출력하지 않고 macOS 기본 `grep -E`로 `environment`와
`alarm_actions_enabled` 줄만 조회했다. AWS와 Terraform 상태에는 영향이 없었다.

### T4. `terraform fmt -check` 출력 해석

`terraform fmt -check versions.tf`가 `versions.tf`를 출력했다. 이는 성공 메시지가 아니라 형식 변경이
필요하다는 뜻이다. Migration을 중단하고 `terraform fmt versions.tf`를 실행한 뒤, 재검사에서 아무
출력도 없음을 확인하고 진행했다.

재발 방지를 위해 `fmt -check`는 파일명 출력 여부뿐 아니라 종료코드를 CI에서 판정해야 한다.

### T5. Migration Prompt의 한글 입력 흔적

State 복사 Prompt에서 한글 입력 흔적 뒤에 `yes`가 입력됐다. Terraform이 최종 `yes`를 인식해 S3
Backend 구성과 초기화를 성공으로 종료했다. 성공 메시지만 믿지 않고 S3 Object Metadata, 활성 Lock
부재, Remote State 주소 12개와 무변경 Plan을 추가로 확인했다.

### T6. 화면 공유 시 식별자와 인증 URL 노출

Discord와 Terminal 화면에는 Account ID, Instance ID, ARN과 일회성 Login URL이 포함될 수 있었다.
Webhook URL, Access Key, Token, Cookie와 Password는 노출되지 않았다. 향후 Issue·PR에는 Account ID,
Instance ID와 ARN을 마스킹하고 Login URL은 첨부하지 않는다.

### T7. Codex 격리 환경에서 Provider Schema 실행 실패

문서 변경 후 Codex의 격리 실행 환경에서 `terraform validate`를 재실행했을 때 macOS ARM64 Provider
Binary가 시작되지 않아 Schema Load가 실패했다. Provider Architecture와 실행 권한은 정상으로
표시됐고, 같은 작업 Directory의 사용자 Terminal에서는 앞서 `terraform validate`가 성공했다.

Backend 선언 추가 후에도 사용자 Terminal의 `terraform init -migrate-state`와 Remote Backend 기준
`terraform plan`이 성공했으며 최종 결과는 `No changes`였다. 따라서 Terraform 구성이나 AWS Drift가
아닌 Codex 실행 Sandbox의 Provider Process 제약으로 분류했다. `terraform fmt -check -recursive`는
Codex 환경에서도 통과했다.

## Rollback과 긴급 중지

### Alarm Action 긴급 중지

오탐이나 알림 폭주 시 Resource를 삭제하지 않고 세 Alarm의 Action만 비활성화한다. 이 작업은 Metric
평가와 Alarm History를 보존한다. 원인을 해결한 뒤 Terraform의 `alarm_actions_enabled`와 실제 상태를
일치시킨다.

### Terraform 기반 복귀

정상 변경 절차에서는 `alarm_actions_enabled=false`로 변경하고 Plan에서 세 Alarm의 Action 제거만
있는지 확인한 후 승인·Apply한다. Application과 EC2는 변경하지 않는다.

### State 복구 경계

- 현재 기준 State는 S3 Remote State다.
- Local State와 Backup은 검증 완료 직후 삭제하지 않고 보존기간을 정한 뒤 정리한다.
- State를 S3 Console이나 `cp` 명령으로 수동 덮어쓰지 않는다.
- 복구가 필요하면 S3 Version, 현재 Lock, State Lineage와 실제 AWS를 먼저 확인하고 별도 승인된
  절차로 복원한다.

## 보안·비용·운영 영향

| 항목 | 영향 |
|---|---|
| 서비스 가용성 | EC2·Container 재시작과 Traffic 중단 없음 |
| Data | Application과 Database Data 변경 없음 |
| 비용 | Alarm 발생 시 SNS·Lambda 호출과 작은 S3 State 요청·저장 비용 추가 |
| Secret | Webhook Value를 조회·출력하지 않았고 Secret ARN만 Terraform이 참조 |
| 권한 | 최종 변경은 Production IAM 사용자로 수행, root로 Resource 변경 없음 |
| 운영 | ALARM뿐 아니라 OK 복구 알림도 수신하며 State의 단일 Mac 의존성 제거 |

## 완료 조건 판정

| 완료 조건 | 판정 |
|---|---|
| Discord 수동 메시지 수신 | 통과 |
| 별도 Plan에서 Alarm Action만 변경 | 통과 |
| 실제 ALARM 메시지 수신 | 통과 |
| 실제 OK 복구 메시지 수신 | 통과 |
| 세 Alarm Action 활성 상태 | 통과 |
| State Remote Backend 이전 | 통과 |
| S3 암호화·Version·Lock 검증 | 통과 |
| Remote State 주소 일치 | 통과 |
| 최종 무변경 Plan | 통과 |

## 남은 작업

1. Local State 원본과 Migration 전 Backup의 보존기간을 정하고 만료 후 안전하게 정리한다.
2. 운영 중 CPU 임계값의 오탐·미탐을 기록하고 실제 Baseline에 맞춰 보정한다.
3. Memory·Disk, 외부 HTTPS, Docker Health와 Lambda 자체 Error Alarm을 우선순위에 따라 추가한다.
4. 이번 작업을 GitHub Issue 또는 기존 Infrastructure Issue의 트러블슈팅 Comment로 남긴다.
5. 다음 Critical Path인 V2 범위·일정 확정과 Foundation Network IaC로 이동한다.

## 공식 근거

- [CloudWatch SetAlarmState CLI](https://docs.aws.amazon.com/cli/latest/reference/cloudwatch/set-alarm-state.html)
- [AWS CLI Console Login과 Logout](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-sign-in.html)
- [Terraform S3 Backend와 Native Lock](https://developer.hashicorp.com/terraform/language/backend/s3)
- [Terraform init과 State Migration](https://developer.hashicorp.com/terraform/cli/commands/init)

## GitHub Issue 초안

### 제목

`[Cloud] V1 장애 알림 활성화 및 Alerting State Remote Backend 이전`

### 본문

```markdown
## 문제와 목적

V1 Production의 SNS→Lambda→Discord 수동 경로는 검증됐지만 CloudWatch Alarm Action이 비활성이고
Terraform State가 한 대의 Mac Local State에 의존했다.

## 영향 범위

- 대상: V1 Production CloudWatch Alarm 3개
- 서비스 재시작 및 Traffic 중단: 없음
- Application·Database Data 변경: 없음

## 적용 내용

- 세 Alarm의 ALARM·OK Action을 기존 SNS Topic에 연결
- TEST ALARM과 실제 Metric 평가에 의한 OK 복구 메시지 검증
- Alerting Local State를 S3 Remote Backend로 이전

## 검증 결과

- Plan: 0 add / 3 change / 0 destroy
- Apply: 0 added / 3 changed / 0 destroyed
- Discord ALARM·OK 수신 성공
- Remote State 주소 12개 일치
- S3 암호화·Version 확인, 활성 Lock 없음
- 최종 Plan: No changes

## 트러블슈팅

- 기본 AWS Profile이 다른 계정을 가리켜 Apply 전 중단
- Login Session 갱신 중 root 주체가 선택됐으나 신원 조회 외 Resource 변경 없이 즉시 로그아웃
- IAM 사용자로 재인증하고 모든 후속 명령에서 Production Profile 명시
- fmt 검사 실패를 Migration 전에 수정

## 재발 방지와 남은 작업

- Apply·Migration 전 Account·ARN·Region·Environment 확인
- root ARN이면 즉시 중단
- Local State 사본의 보존기간 결정 후 정리
- Alarm 임계값을 운영 Baseline으로 보정
```

## PR 본문 초안

```markdown
## 목적

V1 Production Alerting Terraform을 S3 Remote Backend 기준으로 전환하고 운영 문서를 실제 검증 상태와
일치시킵니다.

## 변경 범위

- Alerting Terraform에 S3 Backend 선언 추가
- backend.hcl.example에 Local Production Profile 예시 추가
- Alerting README에 Remote State와 활성화 검증 절차 반영
- CURRENT_STATUS와 2026-10-07 작업·트러블슈팅 기록 추가

## 영향

- Terraform Backend 설정과 문서만 변경
- AWS Resource 추가·삭제 없음
- 실제 Alarm Action은 별도 승인된 Plan으로 이미 활성화 및 검증 완료

## 검증

- terraform fmt -check: 통과
- terraform validate: 사용자 Terminal에서 통과
- terraform init -migrate-state: 통과
- Remote State 주소 12개 확인
- terraform plan -detailed-exitcode: No changes
- Discord ALARM·OK 수신 확인

## Rollback

- Backend 변경은 State 위치와 Lineage를 확인한 별도 migrate-state 절차로만 복귀
- Alarm Action 중지는 Resource 삭제 대신 disable-alarm-actions 또는 승인된 Terraform Plan 사용

Closes #<issue-number>
```
