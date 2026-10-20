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
    error_message = "검토한 운영 계정 ID 12자리 확인 필요."
  }
}
variable "aws_profile" {
  type    = string
  default = "seonjalal-production"
}
variable "enable_frontend_upload" {
  description = "V1 정적 파일 최초 업로드 중에만 활성화. 완료 후 제거."
  type        = bool
  default     = false
}
variable "upload_deadline" {
  description = "임시 업로드 권한 고정 UTC 만료 시각, Plan부터 최대4시간."
  type        = string
  default     = ""
}
variable "candidate_write_gate" {
  description = "검증 중 운영8443에서 POST/PUT/PATCH/DELETE 차단. 최종 전환 승인 전 true 유지."
  type        = bool
  default     = true
}
provider "aws" {
  region              = "ap-northeast-2"
  profile             = var.aws_profile
  allowed_account_ids = [var.aws_account_id]
  default_tags {
    tags = { Project = "seonjalal", Environment = "production", ManagedBy = "terraform" }
  }
}
