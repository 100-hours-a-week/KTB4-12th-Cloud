locals {
  staging_domain_name = "v2-staging.seonjalal.com"
}

# 기존 Public Hosted Zone은 조회만 수행. 새 Zone/사용자 트래픽 레코드 생성 없음.
data "aws_route53_zone" "staging_public" {
  count        = var.enable_staging_certificate ? 1 : 0
  zone_id      = var.staging_public_zone_id
  private_zone = false
}

resource "aws_acm_certificate" "staging" {
  count             = var.enable_staging_certificate ? 1 : 0
  domain_name       = local.staging_domain_name
  validation_method = "DNS"
  key_algorithm     = "RSA_2048"
  lifecycle {
    create_before_destroy = true
    precondition {
      # Provider의 DNS 정규화로 마지막 점이 생략돼도 같은 공개 도메인으로 비교.
      condition     = trimsuffix(lower(data.aws_route53_zone.staging_public[0].name), ".") == "seonjalal.com" && !data.aws_route53_zone.staging_public[0].private_zone
      error_message = "기존 seonjalal.com Public Hosted Zone 확인 필요."
    }
  }
}

resource "aws_route53_record" "staging_certificate_validation" {
  count           = var.enable_staging_certificate ? 1 : 0
  zone_id         = data.aws_route53_zone.staging_public[0].zone_id
  name            = tolist(aws_acm_certificate.staging[0].domain_validation_options)[0].resource_record_name
  type            = "CNAME"
  records         = [tolist(aws_acm_certificate.staging[0].domain_validation_options)[0].resource_record_value]
  ttl             = 60
  allow_overwrite = false
}

# 검증 완료 대기용 논리 리소스. ALB 연결/앱 실행과는 별도 단계.
resource "aws_acm_certificate_validation" "staging" {
  count                   = var.enable_staging_certificate ? 1 : 0
  certificate_arn         = aws_acm_certificate.staging[0].arn
  validation_record_fqdns = [aws_route53_record.staging_certificate_validation[0].fqdn]
  timeouts {
    create = "40m"
  }
}
