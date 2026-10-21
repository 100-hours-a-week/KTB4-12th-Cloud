variable "aws_account_id" {
  type = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "확인한 대상 AWS 계정 ID가 필요합니다."
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
    tags = { Project = "seonjalal", Environment = "staging", Component = "be-split-runtime", ManagedBy = "terraform" }
  }
}

# AI Root의 Namespace/기존 Rehearsal ALB/ASG는 참조만 한다.
# 운영 출시 전에는 deployment=null을 유지하고 Staging 리소스를 생성하지 않는다.
module "backend" {
  count          = var.deployment == null ? 0 : 1
  source         = "../../../modules/be_split_runtime"
  environment    = "staging"
  aws_account_id = var.aws_account_id
  aws_region     = "ap-northeast-2"
  rds_ca_bundle  = file("${path.module}/../rehearsal/certs/ap-northeast-2-bundle.pem")
  config         = var.deployment
}

output "be_split_runtime" {
  value = try(module.backend[0].runtime, null)
}
