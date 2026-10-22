locals {
  # 기존 V1 일회성 업로드 권한은 원래 버전 Prefix에 계속 고정한다.
  asset_prefix = "frontend/production/v1-1.3.2-1c8d3e7232a2"
  # FE CD는 검증한 정적 릴리스를 이 환경 Prefix에 배치한다. 실제 원본 전환은
  # 새 index.html과 자산을 먼저 배치·검증한 후 별도 저장 Plan 승인으로 진행한다.
  static_origin_prefix = "frontend/production"
  bucket_name          = "seonjalal-v2-frontend-${var.aws_account_id}-ap-northeast-2"
}
data "aws_s3_bucket" "frontend" {
  bucket = local.bucket_name
}
data "aws_lb" "shared" {
  name = "seonjalal-v2-staging-alb"
}
data "aws_lb_listener" "production" {
  load_balancer_arn = data.aws_lb.shared.arn
  port              = 8443
}
data "aws_ec2_managed_prefix_list" "cloudfront" {
  name = "com.amazonaws.global.cloudfront.origin-facing"
}
data "aws_iam_role" "source" {
  count = var.enable_frontend_upload ? 1 : 0
  name  = "seonjalal-v1-ec2-role"
}

# 새 공개 경로는 검증 중 쓰기를 먼저 차단. 기존 Staging443 변경 없음.
resource "aws_lb_listener_rule" "write_gate" {
  count        = var.candidate_write_gate ? 1 : 0
  listener_arn = data.aws_lb_listener.production.arn
  priority     = 5
  action {
    type = "fixed-response"
    fixed_response {
      content_type = "application/json"
      message_body = "{\"message\":\"Candidate writes are not enabled\"}"
      status_code  = "503"
    }
  }
  condition {
    http_request_method {
      values = ["POST", "PUT", "PATCH", "DELETE"]
    }
  }
}

# 기존443 제한 유지. CloudFront 원본 IP 범위만 운영8443 허용.
resource "aws_vpc_security_group_ingress_rule" "cloudfront" {
  security_group_id = one(data.aws_lb.shared.security_groups)
  prefix_list_id    = data.aws_ec2_managed_prefix_list.cloudfront.id
  ip_protocol       = "tcp"
  from_port         = 8443
  to_port           = 8443
  depends_on        = [aws_lb_listener_rule.write_gate]
  lifecycle {
    precondition {
      condition     = data.aws_lb.shared.internal && length(data.aws_lb.shared.security_groups) == 1
      error_message = "기존 internal ALB 및 단일 SG 확인 필요."
    }
  }
}

resource "aws_cloudfront_vpc_origin" "backend" {
  vpc_origin_endpoint_config {
    name                   = "seonjalal-v2-production-api"
    arn                    = data.aws_lb.shared.arn
    http_port              = 80
    https_port             = 8443
    origin_protocol_policy = "https-only"
    origin_ssl_protocols {
      items    = ["TLSv1.2"]
      quantity = 1
    }
  }
  depends_on = [aws_vpc_security_group_ingress_rule.cloudfront]
}

resource "aws_cloudfront_origin_access_control" "frontend" {
  name                              = "seonjalal-v2-production-frontend"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}
resource "aws_cloudfront_function" "api" {
  name    = "seonjalal-v2-production-api-path"
  runtime = "cloudfront-js-2.0"
  publish = true
  code    = file("${path.module}/api-path.js")
}
resource "aws_cloudfront_function" "spa" {
  name    = "seonjalal-v2-production-spa"
  runtime = "cloudfront-js-2.0"
  publish = true
  code    = file("${path.module}/spa-path.js")
}

resource "aws_cloudfront_distribution" "production" {
  enabled             = true
  is_ipv6_enabled     = false
  aliases             = ["www.seonjalal.com"]
  comment             = "V1 artifacts on V2; DNS cutover managed separately"
  default_root_object = "index.html"
  price_class         = "PriceClass_200"
  http_version        = "http2"
  wait_for_deployment = true
  origin {
    domain_name              = data.aws_s3_bucket.frontend.bucket_regional_domain_name
    origin_id                = "production-s3"
    origin_path              = "/${local.static_origin_prefix}"
    origin_access_control_id = aws_cloudfront_origin_access_control.frontend.id
  }
  origin {
    domain_name = "v2-origin.seonjalal.com"
    origin_id   = "production-api"
    vpc_origin_config {
      vpc_origin_id            = aws_cloudfront_vpc_origin.backend.id
      origin_read_timeout      = 60
      origin_keepalive_timeout = 5
    }
  }
  default_cache_behavior {
    target_origin_id       = "production-s3"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true
    # 최초 전환에서는 HTML/자산의 오래된 캐시를 배제. 후속 캐시 최적화는 별도.
    cache_policy_id = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"
    function_association {
      event_type   = "viewer-request"
      function_arn = aws_cloudfront_function.spa.arn
    }
  }
  dynamic "ordered_cache_behavior" {
    for_each = ["/api/*", "/api"]
    content {
      path_pattern           = ordered_cache_behavior.value
      target_origin_id       = "production-api"
      viewer_protocol_policy = "redirect-to-https"
      allowed_methods        = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
      cached_methods         = ["GET", "HEAD"]
      compress               = true
      # 캐시 비활성화 및 인증/Cookie/Query 전달. Host는 원본 도메인으로 대체.
      cache_policy_id          = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"
      origin_request_policy_id = "b689b0a8-53d0-40ab-baf2-68738e2966ac"
      function_association {
        event_type   = "viewer-request"
        function_arn = aws_cloudfront_function.api.arn
      }
    }
  }
  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }
  viewer_certificate {
    acm_certificate_arn      = "arn:aws:acm:us-east-1:${var.aws_account_id}:certificate/22593c1a-6937-4dff-a79c-b40e8813586d"
    ssl_support_method       = "sni-only"
    minimum_protocol_version = "TLSv1.2_2021"
  }
  # API403/404를 HTML200으로 바꾸지 않도록 전역 오류 페이지 변환 미설정.
  # S3 Bucket Policy 소유자는 shared/frontend 하나만 유지. 이 Root에서는 변경하지 않음.
}

# 원본 FE를 새 Prefix에만 전달하는 임시 권한. Secret/ECR/DB 권한 없음.
resource "aws_iam_role_policy" "frontend_upload" {
  count = var.enable_frontend_upload ? 1 : 0
  name  = "SeonjalalProductionFrontendBootstrap"
  role  = data.aws_iam_role.source[0].name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = data.aws_s3_bucket.frontend.arn
        Condition = {
          StringLike   = { "s3:prefix" = [local.asset_prefix, "${local.asset_prefix}/*"] }
          DateLessThan = { "aws:CurrentTime" = var.upload_deadline }
        }
      },
      {
        Effect   = "Allow"
        Action   = ["s3:PutObject", "s3:GetObject"]
        Resource = "${data.aws_s3_bucket.frontend.arn}/${local.asset_prefix}/*"
        Condition = {
          DateLessThan = { "aws:CurrentTime" = var.upload_deadline }
        }
      }
    ]
  })
  lifecycle {
    precondition {
      condition = (
        try(timecmp(var.upload_deadline, plantimestamp()), -1) > 0 &&
        try(timecmp(var.upload_deadline, timeadd(plantimestamp(), "4h")), 1) <= 0
      )
      error_message = "업로드 권한에는 현재부터 4시간 이내인 고정 만료 시각 필요."
    }
  }
}
