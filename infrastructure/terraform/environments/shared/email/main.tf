locals {
  config_set_name = "seonjalal-v2-transactional"
  dmarc_value = join("; ", compact([
    "v=DMARC1",
    "p=${var.dmarc_policy}",
    var.dmarc_report_address == null ? "" : "rua=mailto:${var.dmarc_report_address}",
  ]))
}

# 발송 지표(반송·스팸 신고율)를 수집하고 반송·신고 주소는 계정 억제 목록에 올린다.
# TLS를 요구해 암호화되지 않은 전달을 거부한다.
resource "aws_sesv2_configuration_set" "transactional" {
  configuration_set_name = local.config_set_name

  delivery_options {
    tls_policy = "REQUIRE"
  }

  reputation_options {
    reputation_metrics_enabled = true
  }

  sending_options {
    sending_enabled = true
  }

  suppression_options {
    suppressed_reasons = ["BOUNCE", "COMPLAINT"]
  }
}

# 도메인 인증. Easy DKIM(RSA 2048)을 사용하며 인증 레코드는 아래 CNAME 3개다.
# 루트 MX·SPF(ImprovMX 수신 전달)는 건드리지 않는다. DMARC 정렬은 DKIM으로 통과한다.
resource "aws_sesv2_email_identity" "domain" {
  email_identity         = var.domain_name
  configuration_set_name = aws_sesv2_configuration_set.transactional.configuration_set_name

  dkim_signing_attributes {
    next_signing_key_length = "RSA_2048_BIT"
  }
}

# 토큰은 apply 후에 정해지므로 for_each 대신 개수(3개 고정)와 인덱스를 쓴다.
# allow_overwrite를 켜지 않아 같은 이름의 기존 레코드가 있으면 덮어쓰지 않고 실패한다.
resource "aws_route53_record" "dkim" {
  count   = 3
  zone_id = var.hosted_zone_id
  name    = "${aws_sesv2_email_identity.domain.dkim_signing_attributes[0].tokens[count.index]}._domainkey.${var.domain_name}"
  type    = "CNAME"
  ttl     = 300
  records = ["${aws_sesv2_email_identity.domain.dkim_signing_attributes[0].tokens[count.index]}.dkim.amazonses.com"]
}

resource "aws_route53_record" "dmarc" {
  zone_id = var.hosted_zone_id
  name    = "_dmarc.${var.domain_name}"
  type    = "TXT"
  ttl     = 300
  records = [local.dmarc_value]
}

# SMTP는 SendRawEmail을 사용한다. 지정한 발신 주소로만 보내도록 조건을 걸어
# 키가 유출되어도 다른 주소로 위장 발송할 수 없게 한다.
locals {
  send_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid    = "SendOnlyFromServiceAddress"
      Effect = "Allow"
      Action = "ses:SendRawEmail"
      Resource = [
        aws_sesv2_email_identity.domain.arn,
        aws_sesv2_configuration_set.transactional.arn,
      ]
      Condition = {
        StringEquals = { "ses:FromAddress" = var.from_address }
      }
    }]
  })
}

# 사용자와 정책만 만든다. 액세스 키는 State에 비밀이 남지 않도록 Terraform 밖에서 한 번 만든다.
resource "aws_iam_user" "smtp" {
  for_each = var.smtp_user_names
  name     = each.value
  path     = "/service/"
}

resource "aws_iam_user_policy" "smtp" {
  for_each = var.smtp_user_names
  name     = "SendOnlyFromServiceAddress"
  user     = aws_iam_user.smtp[each.key].name
  policy   = local.send_policy
}
