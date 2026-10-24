locals {
  # CloudFront가 ALB로 접근할 때 쓰는 원본 이름. 사용자 접속 이름(v2-staging)은 CloudFront를 가리킨다.
  staging_origin_domain_name = "v2-staging-origin.seonjalal.com"
}

variable "enable_staging_origin_name" {
  type        = bool
  default     = false
  description = "CloudFront 원본 이름(v2-staging-origin)의 ACM 인증서·DNS·ALB 리스너 인증서 준비. 사용자 접속 이름은 변경하지 않음"
}

variable "enable_cloudfront_origin_access" {
  type        = bool
  default     = false
  description = "ALB 443에 CloudFront VPC Origin 서비스 보안 그룹 접근 허용. 접두사 목록 규칙은 SG 규칙 한도(60)를 넘겨 사용하지 않음"
}

variable "staging_user_name_points_to_alb" {
  type        = bool
  default     = true
  description = "사용자 접속 이름(v2-staging)을 내부 ALB 별칭으로 연결. 공개 CloudFront(shared/frontend)가 같은 이름을 소유하게 하려면 먼저 false로 내려 이 레코드를 제거한다(allow_overwrite=false라 겹치면 생성 실패)."
}

resource "aws_acm_certificate" "staging_origin" {
  count             = var.enable_staging_origin_name ? 1 : 0
  domain_name       = local.staging_origin_domain_name
  validation_method = "DNS"
  key_algorithm     = "RSA_2048"
  lifecycle {
    create_before_destroy = true
    precondition {
      condition     = var.enable_staging_certificate
      error_message = "원본 이름 인증서는 기존 Public Hosted Zone 조회(enable_staging_certificate)가 선행되어야 한다."
    }
  }
}

resource "aws_route53_record" "staging_origin_certificate_validation" {
  count           = var.enable_staging_origin_name ? 1 : 0
  zone_id         = data.aws_route53_zone.staging_public[0].zone_id
  name            = tolist(aws_acm_certificate.staging_origin[0].domain_validation_options)[0].resource_record_name
  type            = "CNAME"
  records         = [tolist(aws_acm_certificate.staging_origin[0].domain_validation_options)[0].resource_record_value]
  ttl             = 60
  allow_overwrite = false
}

resource "aws_acm_certificate_validation" "staging_origin" {
  count                   = var.enable_staging_origin_name ? 1 : 0
  certificate_arn         = aws_acm_certificate.staging_origin[0].arn
  validation_record_fqdns = [aws_route53_record.staging_origin_certificate_validation[0].fqdn]
  timeouts {
    create = "40m"
  }
}

# 기본 인증서(v2-staging)는 유지하고, 원본 이름 인증서를 같은 443 리스너에 추가한다(SNI로 선택).
resource "aws_lb_listener_certificate" "staging_origin" {
  count           = var.enable_staging_origin_name && var.enable_alb ? 1 : 0
  listener_arn    = aws_lb_listener.https[0].arn
  certificate_arn = aws_acm_certificate_validation.staging_origin[0].certificate_arn
}

# Internal ALB라 Public DNS에 이름이 있어도 Private IP만 반환한다. Tailscale 기기의 부하테스트 직접 경로로도 쓴다.
resource "aws_route53_record" "staging_origin" {
  count           = var.enable_service && var.enable_staging_origin_name && var.enable_alb ? 1 : 0
  zone_id         = data.aws_route53_zone.staging_public[0].zone_id
  name            = local.staging_origin_domain_name
  type            = "A"
  allow_overwrite = false
  alias {
    name                   = aws_lb.backend[0].dns_name
    zone_id                = aws_lb.backend[0].zone_id
    evaluate_target_health = false
  }
}

# CloudFront VPC Origin을 처음 만들 때 계정이 만드는 서비스 보안 그룹. 운영 VPC Origin 생성 시 이미 존재한다.
data "aws_security_group" "cloudfront_vpc_origin" {
  count  = var.enable_cloudfront_origin_access ? 1 : 0
  vpc_id = var.vpc_id
  filter {
    name   = "group-name"
    values = ["CloudFront-VPCOrigins-Service-SG"]
  }
}

resource "aws_vpc_security_group_ingress_rule" "alb_https_cloudfront_vpc_origin" {
  count                        = var.enable_alb && var.enable_cloudfront_origin_access ? 1 : 0
  security_group_id            = aws_security_group.alb.id
  referenced_security_group_id = data.aws_security_group.cloudfront_vpc_origin[0].id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  description                  = "CloudFront VPC Origin to staging ALB 443"
}
