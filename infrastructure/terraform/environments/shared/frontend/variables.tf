variable "aws_account_id" {
  type    = string
  default = "721744297924"
  validation {
    condition     = var.aws_account_id == "721744297924"
    error_message = "공유 프론트엔드 작업은 검토한 계정에만 허용한다."
  }
}

variable "aws_region" {
  type    = string
  default = "ap-northeast-2"
  validation {
    condition     = var.aws_region == "ap-northeast-2"
    error_message = "공유 정적 파일 버킷은 서울 리전만 허용한다."
  }
}

variable "aws_profile" {
  type    = string
  default = "seonjalal-production"
}

variable "production_distribution_arn" {
  description = "검토한 운영 Distribution의 신규 V1 정적 경로 읽기 허용. null이면 미허용."
  type        = string
  default     = null
  validation {
    condition = var.production_distribution_arn == null ? true : can(
      regex("^arn:aws:cloudfront::721744297924:distribution/[A-Z0-9]+$", var.production_distribution_arn)
    )
    error_message = "검토한 계정의 CloudFront Distribution ARN만 허용한다."
  }
}

variable "staging_cloudfront_enabled" {
  description = "스테이징 전용 CloudFront 구성 활성화. 운영 공개 설정과 별개."
  type        = bool
  default     = false
}

variable "staging_gateway_ipv4" {
  description = "기존 VPN 중계 서버의 실제 인터넷 출구 IPv4 하나. 사용자 IP나 Tailnet IP가 아님."
  type        = string
  default     = null
  validation {
    condition = var.staging_gateway_ipv4 == null ? true : (
      can(cidrnetmask("${var.staging_gateway_ipv4}/32")) &&
      !strcontains(var.staging_gateway_ipv4, "/") &&
      var.staging_gateway_ipv4 != "0.0.0.0"
    )
    error_message = "단일 IPv4 주소만 허용한다. CIDR/전체 허용/IPv6 입력 금지."
  }
}
