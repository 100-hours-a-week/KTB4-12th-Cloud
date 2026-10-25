variable "permissions_policy" {
  description = "브라우저 기능 제한 헤더 값. 운영과 같은 값을 기본으로 사용한다. null이면 헤더를 보내지 않는다."
  type        = string
  default     = "camera=(), microphone=(), geolocation=()"
  validation {
    condition     = var.permissions_policy == null ? true : length(trimspace(var.permissions_policy)) > 0 && !strcontains(var.permissions_policy, "\n")
    error_message = "빈 값이나 줄바꿈이 있는 Permissions-Policy는 허용하지 않는다."
  }
}

# 운영(production/frontend Root)과 같은 헤더를 스테이징 공개 경로에도 붙여 QA 환경이 운영과 같은 응답 정책을 갖게 한다.
# 공개 Distribution을 켤 때만 만든다. 헤더 값과 override 정책은 운영과 동일하며, HSTS는 되돌리기 어려워 포함하지 않는다.
resource "aws_cloudfront_response_headers_policy" "staging_frontend_security" {
  count   = var.staging_public_enabled ? 1 : 0
  name    = "seonjalal-v2-staging-frontend-security"
  comment = "Clickjacking, MIME sniffing, referrer and browser feature restrictions for the staging frontend"

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
