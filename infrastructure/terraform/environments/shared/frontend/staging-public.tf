locals {
  staging_public_domain_name = "v2-staging.seonjalal.com"
  # CloudFront가 ALB로 접근하는 원본 이름. staging/rehearsal Root가 DNS와 ALB 인증서를 소유한다.
  staging_origin_domain_name = "v2-staging-origin.seonjalal.com"
  staging_gate_snippet = var.staging_public_enabled ? templatefile("${path.module}/staging-gate.js.tftpl", {
    token_json = jsonencode(var.staging_gate_token)
  }) : ""
}

variable "staging_public_enabled" {
  description = "화면과 /api를 함께 제공하는 스테이징 공개 Distribution 생성. 기존 제한형 Distribution은 변경하지 않음."
  type        = bool
  default     = false
}

variable "staging_public_zone_id" {
  description = "기존 seonjalal.com Public Hosted Zone ID. 인증서 DNS 검증과 별칭 레코드에 사용."
  type        = string
  default     = null
  validation {
    condition     = var.staging_public_zone_id == null ? true : can(regex("^Z[A-Z0-9]+$", var.staging_public_zone_id))
    error_message = "확인한 기존 Hosted Zone ID만 허용한다."
  }
}

variable "staging_gate_token" {
  description = "접근 게이트 입장 값. 영숫자 32자 이상. Secret이므로 저장소에 두지 않고 로컬 입력으로만 전달."
  type        = string
  default     = null
  sensitive   = true
  validation {
    condition     = var.staging_gate_token == null ? true : can(regex("^[A-Za-z0-9]{32,}$", var.staging_gate_token))
    error_message = "영숫자 32자 이상이어야 한다."
  }
}

data "aws_route53_zone" "staging_public" {
  count        = var.staging_public_enabled ? 1 : 0
  zone_id      = var.staging_public_zone_id
  private_zone = false
}

data "aws_lb" "staging" {
  count = var.staging_public_enabled ? 1 : 0
  name  = "seonjalal-v2-staging-alb"
}

# CloudFront 보기용 인증서는 us-east-1에서 발급해야 한다.
resource "aws_acm_certificate" "staging_public" {
  provider          = aws.us_east_1
  count             = var.staging_public_enabled ? 1 : 0
  domain_name       = local.staging_public_domain_name
  validation_method = "DNS"
  key_algorithm     = "RSA_2048"
  lifecycle {
    create_before_destroy = true
    precondition {
      condition     = trimsuffix(lower(data.aws_route53_zone.staging_public[0].name), ".") == "seonjalal.com" && !data.aws_route53_zone.staging_public[0].private_zone
      error_message = "기존 seonjalal.com Public Hosted Zone 확인 필요."
    }
  }
}

# 인증서 검증 레코드는 만들지 않는다. ACM DNS 검증 레코드는 계정·도메인 단위로 같아서, 같은 도메인의 서울 리전 인증서
# (staging/rehearsal Root 소유)가 이미 같은 이름·값의 레코드를 가지고 있고, 그 레코드가 us-east-1 인증서도 검증한다.
# 같은 레코드를 두 Root가 소유하면 한쪽을 지울 때 다른 쪽 인증서가 깨지므로 여기서는 발급 완료만 기다린다.
resource "aws_acm_certificate_validation" "staging_public" {
  provider        = aws.us_east_1
  count           = var.staging_public_enabled ? 1 : 0
  certificate_arn = aws_acm_certificate.staging_public[0].arn
  timeouts {
    create = "40m"
  }
}

# 운영 VPC Origin과 같은 ALB를 가리키되 443 리스너를 사용한다(운영은 8443).
resource "aws_cloudfront_vpc_origin" "staging_api" {
  count = var.staging_public_enabled ? 1 : 0
  vpc_origin_endpoint_config {
    name                   = "seonjalal-v2-staging-api"
    arn                    = data.aws_lb.staging[0].arn
    http_port              = 80
    https_port             = 443
    origin_protocol_policy = "https-only"
    origin_ssl_protocols {
      items    = ["TLSv1.2"]
      quantity = 1
    }
  }
}

resource "aws_cloudfront_origin_access_control" "staging_public" {
  count                             = var.staging_public_enabled ? 1 : 0
  name                              = "seonjalal-v2-staging-public-frontend"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_function" "staging_web" {
  count   = var.staging_public_enabled ? 1 : 0
  name    = "seonjalal-v2-staging-web"
  runtime = "cloudfront-js-2.0"
  comment = "Staging gate and SPA rewrite"
  publish = true
  code    = templatefile("${path.module}/staging-web.js.tftpl", { gate_snippet = local.staging_gate_snippet })
  lifecycle {
    precondition {
      condition     = var.staging_gate_token != null
      error_message = "공개 전 접근 게이트 값(staging_gate_token)이 필요하다."
    }
  }
}

resource "aws_cloudfront_function" "staging_api" {
  count   = var.staging_public_enabled ? 1 : 0
  name    = "seonjalal-v2-staging-api-path"
  runtime = "cloudfront-js-2.0"
  comment = "Staging gate and API prefix rewrite"
  publish = true
  code    = templatefile("${path.module}/staging-api.js.tftpl", { gate_snippet = local.staging_gate_snippet })
}

resource "aws_cloudfront_distribution" "staging_public" {
  count               = var.staging_public_enabled ? 1 : 0
  enabled             = true
  is_ipv6_enabled     = false
  aliases             = [local.staging_public_domain_name]
  comment             = "Staging public; web + /api behind access gate"
  default_root_object = "index.html"
  price_class         = "PriceClass_200"
  http_version        = "http2"
  wait_for_deployment = true

  origin {
    domain_name              = aws_s3_bucket.frontend.bucket_regional_domain_name
    origin_id                = "staging-s3"
    origin_path              = "/${trimsuffix(local.environment_prefixes.staging, "/")}"
    origin_access_control_id = aws_cloudfront_origin_access_control.staging_public[0].id
  }
  origin {
    domain_name = local.staging_origin_domain_name
    origin_id   = "staging-api"
    vpc_origin_config {
      vpc_origin_id            = aws_cloudfront_vpc_origin.staging_api[0].id
      origin_read_timeout      = 60
      origin_keepalive_timeout = 5
    }
  }

  default_cache_behavior {
    target_origin_id       = "staging-s3"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true
    # CachingDisabled. 스테이징은 새 릴리스 승격 직후 바로 보여야 하므로 캐시하지 않는다.
    cache_policy_id = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"
    function_association {
      event_type   = "viewer-request"
      function_arn = aws_cloudfront_function.staging_web[0].arn
    }
  }
  dynamic "ordered_cache_behavior" {
    for_each = ["/api/*", "/api"]
    content {
      path_pattern           = ordered_cache_behavior.value
      target_origin_id       = "staging-api"
      viewer_protocol_policy = "redirect-to-https"
      allowed_methods        = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
      cached_methods         = ["GET", "HEAD"]
      compress               = true
      # CachingDisabled + AllViewerExceptHostHeader. Host는 원본 이름으로 바뀌어 ALB 호스트 규칙에 맞는다.
      cache_policy_id          = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"
      origin_request_policy_id = "b689b0a8-53d0-40ab-baf2-68738e2966ac"
      function_association {
        event_type   = "viewer-request"
        function_arn = aws_cloudfront_function.staging_api[0].arn
      }
    }
  }
  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }
  viewer_certificate {
    acm_certificate_arn      = aws_acm_certificate_validation.staging_public[0].certificate_arn
    ssl_support_method       = "sni-only"
    minimum_protocol_version = "TLSv1.2_2021"
  }
  tags = {
    Environment = "staging"
  }
}

# 사용자 접속 이름을 CloudFront로 연결한다. 기존 ALB 직접 연결 레코드는 staging/rehearsal Root에서
# 먼저 제거해야 한다(allow_overwrite=false라 겹치면 생성이 실패한다).
resource "aws_route53_record" "staging_public" {
  count           = var.staging_public_enabled ? 1 : 0
  zone_id         = data.aws_route53_zone.staging_public[0].zone_id
  name            = local.staging_public_domain_name
  type            = "A"
  allow_overwrite = false
  alias {
    name                   = aws_cloudfront_distribution.staging_public[0].domain_name
    zone_id                = aws_cloudfront_distribution.staging_public[0].hosted_zone_id
    evaluate_target_health = false
  }
}
