terraform {
  required_version = ">= 1.10.0, < 2.0.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.67.0"
    }
  }
  # 기존 Foundation과 다른 Key 사용 필수. 기존 State 이전/재사용 금지.
  backend "s3" {}
}

variable "aws_account_id" {
  type        = string
  description = "사용자가 확인한 운영 AWS 계정 ID"
  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "AWS 계정 ID는 12자리 숫자여야 합니다."
  }
}

variable "aws_profile" {
  type        = string
  description = "운영 변경에 사용하는 AWS CLI 프로파일"
  default     = "seonjalal-production"
}

provider "aws" {
  region              = "ap-northeast-2"
  profile             = var.aws_profile
  allowed_account_ids = [var.aws_account_id]
  default_tags {
    tags = { Project = "seonjalal", Environment = "production", ManagedBy = "terraform" }
  }
}

# CloudFront 사용자 HTTPS 인증서는 버지니아 북부 리전에 발급.
provider "aws" {
  alias               = "virginia"
  region              = "us-east-1"
  profile             = var.aws_profile
  allowed_account_ids = [var.aws_account_id]
  default_tags {
    tags = { Project = "seonjalal", Environment = "production", ManagedBy = "terraform" }
  }
}
