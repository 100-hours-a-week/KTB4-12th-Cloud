# 앱 시험용 이름만 연결. Public DNS에 이름이 있어도 Internal ALB는 Private IP만 사용.
# V1/dev/운영 레코드나 기존 검증 CNAME은 변경하지 않음.
resource "aws_route53_record" "staging_backend" {
  count           = var.enable_service && var.enable_staging_certificate && var.enable_alb && var.staging_user_name_points_to_alb ? 1 : 0
  zone_id         = data.aws_route53_zone.staging_public[0].zone_id
  name            = local.staging_domain_name
  type            = "A"
  allow_overwrite = false
  alias {
    name                   = aws_lb.backend[0].dns_name
    zone_id                = aws_lb.backend[0].zone_id
    evaluate_target_health = false
  }
}
