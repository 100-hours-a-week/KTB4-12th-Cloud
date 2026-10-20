# 실제 AWS 호출 없이 Plan의 도메인·DNS 변경 범위 검사.
mock_provider "aws" {
  override_during = plan
}
mock_provider "aws" {
  alias           = "virginia"
  override_during = plan
}

override_data {
  target = data.aws_route53_zone.public
  values = {
    zone_id      = "ZMOCKPUBLIC"
    name         = "seonjalal.com."
    private_zone = false
  }
}

override_resource {
  target = aws_acm_certificate.origin
  values = {
    arn = "arn:aws:acm:ap-northeast-2:123456789012:certificate/11111111-1111-1111-1111-111111111111"
    domain_validation_options = [{
      domain_name           = "v2-origin.seonjalal.com"
      resource_record_name  = "_origin.v2-origin.seonjalal.com."
      resource_record_type  = "CNAME"
      resource_record_value = "_origin.acm-validations.aws."
    }]
  }
}

override_resource {
  target = aws_acm_certificate.viewer
  values = {
    arn = "arn:aws:acm:us-east-1:123456789012:certificate/22222222-2222-2222-2222-222222222222"
    domain_validation_options = [
      {
        domain_name           = "www.seonjalal.com"
        resource_record_name  = "_www.www.seonjalal.com."
        resource_record_type  = "CNAME"
        resource_record_value = "_www.acm-validations.aws."
      },
      {
        domain_name           = "seonjalal.com"
        resource_record_name  = "_apex.seonjalal.com."
        resource_record_type  = "CNAME"
        resource_record_value = "_apex.acm-validations.aws."
      }
    ]
  }
}

run "only_validation_dns_and_unattached_certificates" {
  command = plan
  variables {
    aws_account_id = "123456789012"
  }

  assert {
    condition = (
      aws_acm_certificate.origin.domain_name == "v2-origin.seonjalal.com" &&
      aws_acm_certificate.viewer.domain_name == "www.seonjalal.com" &&
      toset(aws_acm_certificate.viewer.subject_alternative_names) == toset(["seonjalal.com"])
    )
    error_message = "승인 대상 운영 도메인 범위 불일치."
  }

  assert {
    condition = (
      length(aws_route53_record.viewer_validation) == 2 &&
      aws_route53_record.origin_validation.type == "CNAME" &&
      !aws_route53_record.origin_validation.allow_overwrite &&
      alltrue([for record in aws_route53_record.viewer_validation : record.type == "CNAME" && !record.allow_overwrite])
    )
    error_message = "검증 CNAME 3개 및 기존 레코드 덮어쓰기 금지 조건 불일치."
  }

  assert {
    condition = (
      !output.certificates.application_ready &&
      !output.certificates.traffic_switched &&
      !output.certificates.database_switched
    )
    error_message = "인증서 준비를 앱 또는 DB 전환 완료로 표시하면 안 됩니다."
  }
}
