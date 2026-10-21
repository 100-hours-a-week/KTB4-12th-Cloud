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
    tags = { Project = "seonjalal", Environment = "production", Component = "ai-runtime", ManagedBy = "terraform" }
  }
}

# 이미지·모델/미디어·DB Role·Secret 준비가 없으면 빈 Task/Service를 등록하지 않는다.
module "ai" {
  count          = var.deployment == null ? 0 : 1
  source         = "../../../modules/ai_ecs_runtime"
  environment    = "production"
  aws_account_id = var.aws_account_id
  aws_region     = "ap-northeast-2"
  # 서울 RDS 공개 CA Bundle은 스테이징/운영에서 동일한 파일을 재사용한다.
  rds_ca_bundle = file("${path.module}/../../staging/rehearsal/certs/ap-northeast-2-bundle.pem")
  config        = var.deployment
}

output "ai_runtime" {
  value = try(module.ai[0].runtime, null)
}
