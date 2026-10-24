terraform {
  required_version = ">= 1.10.0, < 2.0.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.67.0"
    }
  }
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

variable "node_capacity" {
  description = "BE ECS 호스트 수(min=desired=max). 기본 2대. 블루-그린 전환처럼 새 Task를 옆에 띄울 때만 임시로 4대까지 늘리고 완료 후 2로 되돌린다."
  type        = number
  default     = 2
  validation {
    condition     = contains([2, 3, 4], var.node_capacity)
    error_message = "운영 BE 호스트는 2~4대만 허용한다."
  }
}

variable "ai_desired_capacity" {
  description = "AI 전용 ECS 호스트 수. 기본 0대, 승인 후 최대 1대"
  type        = number
  default     = 0
  validation {
    condition     = contains([0, 1], var.ai_desired_capacity)
    error_message = "운영 AI 전용 호스트는 0대 또는 1대만 허용."
  }
}

provider "aws" {
  region              = "ap-northeast-2"
  profile             = var.aws_profile
  allowed_account_ids = [var.aws_account_id]
  default_tags {
    tags = { Project = "seonjalal", Environment = "production", ManagedBy = "terraform" }
  }
}
