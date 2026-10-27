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

variable "bootstrap_admin_secret_arn" {
  type        = string
  default     = null
  description = "RDS 관리형 ai_admin Secret ARN. 미설정이면 일회성 Bootstrap Task를 등록하지 않음"
  validation {
    condition = var.bootstrap_admin_secret_arn == null || can(regex(
      "^arn:aws:secretsmanager:ap-northeast-2:[0-9]{12}:secret:rds![A-Za-z0-9/_+=.@-]+$",
      var.bootstrap_admin_secret_arn
    ))
    error_message = "서울 리전의 RDS 관리형 Secret ARN만 허용. 비밀번호를 입력하지 마세요."
  }
}

variable "bootstrap_expires_at" {
  type        = string
  default     = null
  description = "일회성 Bootstrap의 IAM Secret 접근 만료 UTC 시각(현재부터 4시간 이내). 관리자 Secret ARN을 설정하면 필수"
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
