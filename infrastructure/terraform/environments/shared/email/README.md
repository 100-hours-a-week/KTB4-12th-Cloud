# 공유 메일 발송(SES) 설정

- 독립 State: `v2/shared/email/terraform.tfstate`. 스테이징·운영·로컬 개발이 같은 발신 도메인을 사용
- 리전: 서울(`ap-northeast-2`). 앱과 같은 리전이며 샌드박스 해제도 이 리전 기준으로 신청
- 생성: SES 도메인 ID(`seonjalal.com`, Easy DKIM RSA 2048), DKIM CNAME 3개, `_dmarc` TXT(기본 `p=none`), Configuration Set, 발송 전용 IAM 사용자 2개(로컬·개발용, 운영용)와 정책
- 호스팅 영역은 이 Root가 소유하지 않는다. 입력(`hosted_zone_id`)으로 받은 기존 영역에 레코드만 추가
- 루트 MX·SPF(ImprovMX 수신 전달)는 변경하지 않는다. DMARC 정렬은 DKIM으로 통과
- 기존 같은 이름 레코드를 덮어쓰지 않는다(`allow_overwrite` 미사용)

## 이 Root가 하지 않는 것

- 샌드박스 해제 신청: 계정 단위 요청이라 CLI로 별도 진행(`aws sesv2 put-account-details`), 신청 문구는 사용자 승인 후 제출
- 액세스 키 생성: Terraform State에 비밀이 남지 않도록 CLI로 만들고 Secrets Manager에 바로 저장
- Backend 환경 변수 연결과 Main 재배포: 별도 승인 단계

## 발송 권한

- IAM 사용자는 `ses:SendRawEmail`만 허용하며 `ses:FromAddress`가 `no-reply@seonjalal.com`일 때만 통과
- 대상 리소스는 도메인 ID와 Configuration Set으로 한정. 키가 유출되어도 다른 주소로 위장 발송 불가
- 로컬·개발용과 운영용을 분리해 한쪽만 폐기 가능

## 앱 환경 변수 매핑(운영 발송 권한 승인 후)

| 변수 | 값 | 출처 |
|---|---|---|
| `SMTP_HOST` | `email-smtp.ap-northeast-2.amazonaws.com` | 출력 `ses.smtp_host` |
| `SMTP_PORT` | `587` | 고정 |
| `SMTP_USERNAME` | IAM 액세스 키 ID | Secrets Manager |
| `SMTP_PASSWORD` | 액세스 키에서 계산한 SES SMTP 비밀번호 | Secrets Manager |
| `SMTP_AUTH` | `true` | 고정 |
| `SMTP_STARTTLS_ENABLE` | `true` | 고정 |
| `SMTP_FROM_ADDRESS` | `no-reply@seonjalal.com` | 입력 `from_address` |

## 적용 순서

1. `terraform plan -out`으로 변경 확인(예상: 추가 약 11개, 변경·삭제 0). 기존 레코드 변경이 보이면 중단
2. 승인 후 apply
3. `aws sesv2 get-email-identity --email-identity seonjalal.com --region ap-northeast-2`로 DKIM `SUCCESS` 확인
4. 샌드박스 해제 신청, 승인 확인
5. 액세스 키 생성과 Secrets Manager 저장, 앱 연결

## 검증

- `terraform test`: Mock Provider, 아홉 가지(DKIM·레코드 이름, 루트 레코드 미관리, DMARC 기본·보고서, Configuration Set, IAM 제한, 잘못된 입력 거부)
- 실제 발송과 DKIM 검증 결과는 apply 후 확인 필요(코드 검증만으로는 증명되지 않음)
