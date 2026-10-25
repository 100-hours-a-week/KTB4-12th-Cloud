variable "aws_account_id" {
  type    = string
  default = "721744297924"
  validation {
    condition     = var.aws_account_id == "721744297924"
    error_message = "공유 메일 발송 설정은 검토한 계정에만 허용한다."
  }
}

variable "aws_region" {
  type    = string
  default = "ap-northeast-2"
  validation {
    condition     = var.aws_region == "ap-northeast-2"
    error_message = "SES 발송과 샌드박스 해제 신청은 앱과 같은 서울 리전 기준으로 진행한다."
  }
}

variable "aws_profile" {
  type    = string
  default = "seonjalal-production"
}

variable "hosted_zone_id" {
  description = "발신 도메인의 기존 Route53 호스팅 영역 ID. 영역은 이 Root가 소유하지 않고 레코드만 추가한다."
  type        = string
  validation {
    condition     = can(regex("^Z[A-Z0-9]+$", var.hosted_zone_id))
    error_message = "Route53 호스팅 영역 ID 형식이어야 한다."
  }
}

variable "domain_name" {
  type    = string
  default = "seonjalal.com"
  validation {
    condition     = var.domain_name == "seonjalal.com"
    error_message = "인증하는 발신 도메인은 seonjalal.com만 허용한다."
  }
}

variable "from_address" {
  description = "SMTP 사용자가 보낼 수 있는 유일한 발신 주소."
  type        = string
  default     = "no-reply@seonjalal.com"
  validation {
    condition     = endswith(var.from_address, "@seonjalal.com")
    error_message = "발신 주소는 인증한 도메인에 속해야 한다."
  }
}

variable "dmarc_policy" {
  description = "none은 모니터링만 하고 메일을 막지 않는다. 발송 결과를 확인한 뒤 올릴 수 있다."
  type        = string
  default     = "none"
  validation {
    condition     = contains(["none", "quarantine", "reject"], var.dmarc_policy)
    error_message = "DMARC 정책은 none, quarantine, reject 중 하나여야 한다."
  }
}

variable "dmarc_report_address" {
  description = "DMARC 집계 보고서를 받을 주소. null이면 보고서 수신 설정을 넣지 않는다."
  type        = string
  default     = null
  validation {
    condition     = var.dmarc_report_address == null ? true : can(regex("^[^@\\s]+@[^@\\s]+$", var.dmarc_report_address))
    error_message = "보고서 주소는 이메일 형식이어야 한다."
  }
}

variable "smtp_user_names" {
  description = "발송용 IAM 사용자. 로컬·개발 확인용과 운영용을 분리해 한쪽만 폐기할 수 있게 한다."
  type        = map(string)
  default = {
    local      = "seonjalal-v2-ses-smtp-local"
    production = "seonjalal-v2-ses-smtp-production"
  }
}
