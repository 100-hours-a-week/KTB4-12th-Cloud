mock_provider "aws" {
  mock_resource "aws_sesv2_email_identity" {
    defaults = {
      arn = "arn:aws:ses:ap-northeast-2:721744297924:identity/seonjalal.com"
      dkim_signing_attributes = {
        tokens = ["tokenaaa", "tokenbbb", "tokenccc"]
      }
    }
  }
  mock_resource "aws_sesv2_configuration_set" {
    defaults = {
      arn = "arn:aws:ses:ap-northeast-2:721744297924:configuration-set/seonjalal-v2-transactional"
    }
  }
}

variables {
  hosted_zone_id = "ZMOCKZONE123"
}

run "domain_identity_uses_easy_dkim_and_three_records" {
  command = apply
  assert {
    condition = (
      aws_sesv2_email_identity.domain.email_identity == "seonjalal.com" &&
      aws_sesv2_email_identity.domain.dkim_signing_attributes[0].next_signing_key_length == "RSA_2048_BIT" &&
      length(aws_route53_record.dkim) == 3 &&
      alltrue([for r in aws_route53_record.dkim : r.type == "CNAME" && endswith(r.name, "._domainkey.seonjalal.com") && endswith(one(r.records), ".dkim.amazonses.com")]) &&
      aws_route53_record.dkim[0].name == "tokenaaa._domainkey.seonjalal.com" &&
      aws_route53_record.dkim[2].name == "tokenccc._domainkey.seonjalal.com"
    )
    error_message = "도메인 ID는 DKIM 2048비트이고 토큰별 CNAME 3개가 올바른 이름·값이어야 한다."
  }
}

run "existing_apex_mail_records_are_not_managed" {
  command = apply
  assert {
    condition = (
      alltrue([for r in aws_route53_record.dkim : r.name != "seonjalal.com"]) &&
      aws_route53_record.dmarc.name == "_dmarc.seonjalal.com" &&
      aws_route53_record.dmarc.type == "TXT"
    )
    error_message = "루트 MX·SPF는 건드리지 않고 _dmarc와 _domainkey 하위 레코드만 추가해야 한다."
  }
}

run "dmarc_defaults_to_monitoring_only" {
  command = apply
  assert {
    condition     = one(aws_route53_record.dmarc.records) == "v=DMARC1; p=none"
    error_message = "기본 DMARC는 모니터링만(p=none)이고 보고서 주소가 없으면 rua를 넣지 않아야 한다."
  }
}

run "dmarc_report_address_is_added_when_set" {
  command = apply
  variables {
    dmarc_policy         = "quarantine"
    dmarc_report_address = "dmarc@seonjalal.com"
  }
  assert {
    condition     = one(aws_route53_record.dmarc.records) == "v=DMARC1; p=quarantine; rua=mailto:dmarc@seonjalal.com"
    error_message = "정책과 보고서 주소가 DMARC 값에 반영되어야 한다."
  }
}

run "configuration_set_protects_reputation" {
  command = apply
  assert {
    condition = (
      aws_sesv2_configuration_set.transactional.delivery_options[0].tls_policy == "REQUIRE" &&
      aws_sesv2_configuration_set.transactional.reputation_options[0].reputation_metrics_enabled == true &&
      toset(aws_sesv2_configuration_set.transactional.suppression_options[0].suppressed_reasons) == toset(["BOUNCE", "COMPLAINT"]) &&
      aws_sesv2_email_identity.domain.configuration_set_name == "seonjalal-v2-transactional"
    )
    error_message = "TLS 필수, 평판 지표 수집, 반송·신고 주소 억제가 켜져 있어야 한다."
  }
}

run "smtp_users_can_only_send_from_service_address" {
  command = apply
  assert {
    condition = (
      toset(keys(aws_iam_user.smtp)) == toset(["local", "production"]) &&
      aws_iam_user.smtp["local"].name != aws_iam_user.smtp["production"].name &&
      alltrue([for k, p in aws_iam_user_policy.smtp :
        jsondecode(p.policy).Statement[0].Action == "ses:SendRawEmail" &&
        jsondecode(p.policy).Statement[0].Condition.StringEquals["ses:FromAddress"] == "no-reply@seonjalal.com" &&
        length(jsondecode(p.policy).Statement) == 1 &&
        !contains(jsondecode(p.policy).Statement[0].Resource, "*")
      ])
    )
    error_message = "로컬·운영 사용자가 분리되고 SendRawEmail만, 지정한 발신 주소와 정확한 리소스로 제한되어야 한다."
  }
}

run "rejects_unreviewed_account" {
  command = plan
  variables {
    aws_account_id = "111111111111"
  }
  expect_failures = [var.aws_account_id]
}

run "rejects_from_address_outside_domain" {
  command = plan
  variables {
    from_address = "no-reply@example.com"
  }
  expect_failures = [var.from_address]
}

run "rejects_unknown_dmarc_policy" {
  command = plan
  variables {
    dmarc_policy = "monitor"
  }
  expect_failures = [var.dmarc_policy]
}
