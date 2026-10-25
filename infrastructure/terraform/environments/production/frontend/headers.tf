variable "permissions_policy" {
  description = "브라우저 기능 제한 헤더 값. 카메라·마이크·위치를 쓰는 화면이 생기면 해당 항목을 제외한다. null이면 헤더를 보내지 않는다."
  type        = string
  default     = "camera=(), microphone=(), geolocation=()"
  validation {
    condition     = var.permissions_policy == null ? true : length(trimspace(var.permissions_policy)) > 0 && !strcontains(var.permissions_policy, "\n")
    error_message = "빈 값이나 줄바꿈이 있는 Permissions-Policy는 허용하지 않는다."
  }
}

# S3 정적 파일 응답에는 보안 헤더가 없으므로(FE Nginx 설정은 이 경로에 적용되지 않음) CloudFront 응답 계층에서 붙인다.
# 모든 헤더는 override = true: 원본이 같은 헤더를 보내더라도 이 값으로 통일한다.
# HSTS는 브라우저가 기간 동안 기억해 되돌리기 어려우므로 이 변경에 포함하지 않는다(별도 검토).
resource "aws_cloudfront_response_headers_policy" "frontend_security" {
  name    = "seonjalal-v2-production-frontend-security"
  comment = "Clickjacking, MIME sniffing, referrer and browser feature restrictions for the static frontend"

  security_headers_config {
    content_type_options {
      override = true
    }
    frame_options {
      frame_option = "SAMEORIGIN"
      override     = true
    }
    referrer_policy {
      referrer_policy = "strict-origin-when-cross-origin"
      override        = true
    }
    # 기존 CSP가 없어 병합 대상이 없다. 다른 CSP 지시어를 추가할 때는 이 값에 합쳐야 한다(헤더가 둘이면 모두 적용됨).
    content_security_policy {
      content_security_policy = "frame-ancestors 'self'"
      override                = true
    }
  }

  dynamic "custom_headers_config" {
    for_each = var.permissions_policy == null ? [] : [var.permissions_policy]
    content {
      items {
        header   = "Permissions-Policy"
        value    = custom_headers_config.value
        override = true
      }
    }
  }
}
