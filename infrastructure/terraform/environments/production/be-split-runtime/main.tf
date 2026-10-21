variable "aws_account_id" {
  type = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "확인한 대상 AWS 계정 ID 12자리 필요."
  }
}

variable "aws_profile" {
  type    = string
  default = null
}

variable "deployment" {
  type        = any
  default     = null
  description = "기본 null은 리소스 미생성. 실제 입력은 be_split_runtime 모듈에서 검증"
}

provider "aws" {
  region              = "ap-northeast-2"
  profile             = var.aws_profile
  allowed_account_ids = [var.aws_account_id]
  default_tags {
    tags = { Project = "seonjalal", Environment = "production", Component = "be-split-runtime", ManagedBy = "terraform" }
  }
}

# 기존 운영 단일 BE Service/ALB 기본 Target을 유지한다. 실제 입력 전에는 리소스 0개.
module "backend" {
  count          = var.deployment == null ? 0 : 1
  source         = "../../../modules/be_split_runtime"
  environment    = "production"
  aws_account_id = var.aws_account_id
  aws_region     = "ap-northeast-2"
  rds_ca_bundle  = file("${path.module}/../../staging/rehearsal/certs/ap-northeast-2-bundle.pem")
  config         = var.deployment
}

output "be_split_runtime" {
  value = try(module.backend[0].runtime, null)
}
