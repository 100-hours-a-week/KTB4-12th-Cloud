terraform {
  required_version = ">= 1.10.0, < 2.0.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.67.0"
    }
  }
  # 기존 ALB 소유 State와 분리. 기존 ALB는 data로만 참조.
  backend "s3" {}
}

variable "aws_account_id" {
  type = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "운영 계정 ID 12자리 확인 필요."
  }
}

variable "aws_profile" {
  type    = string
  default = "seonjalal-production"
}

provider "aws" {
  region              = "ap-northeast-2"
  profile             = var.aws_profile
  allowed_account_ids = [var.aws_account_id]
  default_tags {
    tags = { Project = "seonjalal", Environment = "production", ManagedBy = "terraform" }
  }
}
