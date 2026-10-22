variable "aws_account_id" {
  type = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "확인한 AWS 계정 ID 12자리 필요."
  }
}
variable "aws_profile" {
  type    = string
  default = null
}
variable "deployment" {
  type        = any
  default     = null
  description = "기본 null은 리소스 미생성. 실제 입력은 ai_ecs_runtime 모듈의 config 계약으로 검증"
}

provider "aws" {
  region              = "ap-northeast-2"
  profile             = var.aws_profile
  allowed_account_ids = [var.aws_account_id]
  default_tags {
    tags = { Project = "seonjalal", Environment = "staging", Component = "ai-runtime", ManagedBy = "terraform" }
  }
}

# 운영 출시 우선: 빈 입력으로 가짜 DB/Secret/Image를 등록하지 않는다.
module "ai" {
  count          = var.deployment == null ? 0 : 1
  source         = "../../../modules/ai_ecs_runtime"
  environment    = "staging"
  aws_account_id = var.aws_account_id
  aws_region     = "ap-northeast-2"
  rds_ca_bundle  = file("${path.module}/../rehearsal/certs/ap-northeast-2-bundle.pem")
  config         = var.deployment
}

output "ai_runtime" {
  value = try(module.ai[0].runtime, null)
}
