locals {
  zone_name      = "seonjalal.com"
  origin_domain  = "v2-origin.seonjalal.com"
  viewer_domains = toset(["www.seonjalal.com", "seonjalal.com"])
}

# 기존 공개 Zone 조회만 수행. 새 Zone 또는 서비스 A/AAAA/Alias 생성 없음.
data "aws_route53_zone" "public" {
  name         = local.zone_name
  private_zone = false
}

resource "aws_acm_certificate" "origin" {
  domain_name       = local.origin_domain
  validation_method = "DNS"
  key_algorithm     = "RSA_2048"
  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_acm_certificate" "viewer" {
  provider                  = aws.virginia
  domain_name               = "www.seonjalal.com"
  subject_alternative_names = ["seonjalal.com"]
  validation_method         = "DNS"
  key_algorithm             = "RSA_2048"
  lifecycle {
    create_before_destroy = true
  }
}

# 소유권 검증용 CNAME만 추가. 기존 레코드와 충돌하면 덮어쓰지 않고 중단.
resource "aws_route53_record" "origin_validation" {
  zone_id         = data.aws_route53_zone.public.zone_id
  name            = one(aws_acm_certificate.origin.domain_validation_options).resource_record_name
  type            = "CNAME"
  records         = [one(aws_acm_certificate.origin.domain_validation_options).resource_record_value]
  ttl             = 60
  allow_overwrite = false
}

# 반복 키는 발급 전에도 확정되는 도메인명 사용. ACM 응답 순서와 무관하게 연결.
resource "aws_route53_record" "viewer_validation" {
  for_each = local.viewer_domains
  zone_id  = data.aws_route53_zone.public.zone_id
  name = one([
    for option in aws_acm_certificate.viewer.domain_validation_options : option.resource_record_name
    if option.domain_name == each.key
  ])
  type = "CNAME"
  records = [one([
    for option in aws_acm_certificate.viewer.domain_validation_options : option.resource_record_value
    if option.domain_name == each.key
  ])]
  ttl             = 60
  allow_overwrite = false
}

# 발급 완료 대기용 논리 리소스. ALB/CloudFront 연결 또는 트래픽 전환 없음.
resource "aws_acm_certificate_validation" "origin" {
  certificate_arn         = aws_acm_certificate.origin.arn
  validation_record_fqdns = [aws_route53_record.origin_validation.fqdn]
  timeouts {
    create = "40m"
  }
}

resource "aws_acm_certificate_validation" "viewer" {
  provider        = aws.virginia
  certificate_arn = aws_acm_certificate.viewer.arn
  validation_record_fqdns = [
    for record in aws_route53_record.viewer_validation : record.fqdn
  ]
  timeouts {
    create = "40m"
  }
}
