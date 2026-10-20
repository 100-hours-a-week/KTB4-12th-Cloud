# 현재 단계는 정적 파일 전달만 구성한다. API와 VPN 진입 포트는 후속 단계.
resource "aws_cloudfront_origin_access_control" "staging" {
  count                             = var.staging_cloudfront_enabled ? 1 : 0
  name                              = "seonjalal-v2-staging-frontend"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

resource "aws_cloudfront_function" "staging_viewer" {
  count   = var.staging_cloudfront_enabled ? 1 : 0
  name    = "seonjalal-v2-staging-viewer"
  runtime = "cloudfront-js-2.0"
  comment = "Allow staging static requests from VPN gateway egress only"
  publish = true
  code = templatefile("${path.module}/staging-viewer.js.tftpl", {
    allowed_ipv4_json = jsonencode(var.staging_gateway_ipv4)
  })

  lifecycle {
    precondition {
      condition     = var.staging_gateway_ipv4 != null
      error_message = "CloudFront 생성 전 VPN 중계 서버의 실제 출구 IPv4 확인 필수."
    }
  }
}

resource "aws_cloudfront_distribution" "staging" {
  count               = var.staging_cloudfront_enabled ? 1 : 0
  enabled             = true
  is_ipv6_enabled     = false
  comment             = "Staging static only; VPN gateway egress restricted"
  default_root_object = "index.html"
  price_class         = "PriceClass_200"
  http_version        = "http2"
  wait_for_deployment = true

  origin {
    domain_name              = aws_s3_bucket.frontend.bucket_regional_domain_name
    origin_id                = "staging-s3"
    origin_path              = "/${trimsuffix(local.environment_prefixes.staging, "/")}"
    origin_access_control_id = aws_cloudfront_origin_access_control.staging[0].id
    # OAC 사용. Provider가 읽을 때 생략하는 빈 OAI 블록을 선언하면 영구 diff 발생.
    # S3 원본의 빈 API 설정은 Provider가 요청 생성 시 처리한다.
  }

  default_cache_behavior {
    target_origin_id       = "staging-s3"
    viewer_protocol_policy = "https-only"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true
    # 첫 연결 시험은 HTML 캐시·쿠키 전달을 배제한다. 성능용 캐시 정책은 후속 검증.
    min_ttl     = 0
    default_ttl = 0
    max_ttl     = 0
    forwarded_values {
      query_string = false
      cookies {
        forward = "none"
      }
    }
    function_association {
      event_type   = "viewer-request"
      function_arn = aws_cloudfront_function.staging_viewer[0].arn
    }
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }
  viewer_certificate {
    cloudfront_default_certificate = true
  }
  tags = {
    Environment = "staging"
  }
}

# 공유 버킷 정책의 소유자는 이 Root 하나다. 운영 Distribution 추가 시 이 문서에 별도 Statement 추가.
resource "aws_s3_bucket_policy" "frontend_cloudfront" {
  count  = var.staging_cloudfront_enabled || var.production_distribution_arn != null ? 1 : 0
  bucket = aws_s3_bucket.frontend.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat(var.staging_cloudfront_enabled ? [{
      Sid       = "StagingCloudFrontReadOnly"
      Effect    = "Allow"
      Principal = { Service = "cloudfront.amazonaws.com" }
      Action    = "s3:GetObject"
      Resource  = "${aws_s3_bucket.frontend.arn}/${local.environment_prefixes.staging}*"
      Condition = {
        StringEquals = {
          "AWS:SourceArn" = aws_cloudfront_distribution.staging[0].arn
        }
      }
      }] : [], var.production_distribution_arn != null ? [{
      Sid       = "ProductionCloudFrontReadOnly"
      Effect    = "Allow"
      Principal = { Service = "cloudfront.amazonaws.com" }
      Action    = "s3:GetObject"
      Resource  = "${aws_s3_bucket.frontend.arn}/frontend/production/v1-1.3.2-1c8d3e7232a2/*"
      Condition = {
        StringEquals = {
          "AWS:SourceArn" = var.production_distribution_arn
        }
      }
    }] : [])
  })
  depends_on = [aws_s3_bucket_public_access_block.frontend]
}
