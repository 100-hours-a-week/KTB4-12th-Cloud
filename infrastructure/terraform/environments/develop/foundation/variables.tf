variable "aws_account_id" {
  description = "V2 Develop을 구축할 AWS Account ID"
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "aws_account_id must be a 12-digit AWS account ID."
  }
}

variable "application_port" {
  description = "ALB가 V2 Backend Task로 전달할 Port"
  type        = number
  default     = 8080

  validation {
    condition     = var.application_port >= 1 && var.application_port <= 65535
    error_message = "application_port는 1~65535 범위여야 합니다."
  }
}

variable "aws_region" {
  description = "V2 Develop의 AWS Region"
  type        = string
  default     = "ap-northeast-2"

  validation {
    condition     = var.aws_region == "ap-northeast-2"
    error_message = "V2 Develop의 기준 Region은 ap-northeast-2입니다."
  }
}

variable "aws_profile" {
  description = "Local Plan에서만 사용할 AWS CLI Profile. CI에서는 OIDC Role을 사용한다."
  type        = string
  default     = null
  nullable    = true
}

variable "environment" {
  description = "이 Root Module이 관리하는 환경"
  type        = string
  default     = "develop"

  validation {
    condition     = var.environment == "develop"
    error_message = "이 Root Module은 develop 환경만 관리합니다."
  }
}

variable "vpc_cidr" {
  description = "기본 VPC와 분리한 V2 전용 VPC CIDR"
  type        = string
  default     = "10.20.0.0/16"

  validation {
    condition     = can(cidrnetmask(var.vpc_cidr))
    error_message = "vpc_cidr must be a valid IPv4 CIDR."
  }
}

variable "additional_tags" {
  description = "공통 Tag에 추가할 선택 Tag"
  type        = map(string)
  default     = {}
}

variable "nat_gateway_mode" {
  description = "Private App Subnet의 Egress 방식. Develop은 single, Production은 per_az를 권장한다."
  type        = string
  default     = "none"

  validation {
    condition     = contains(["none", "single", "per_az"], var.nat_gateway_mode)
    error_message = "nat_gateway_mode는 none, single, per_az 중 하나여야 합니다."
  }
}
