variable "aws_account_id" {
  type = string
  validation {
    condition     = var.aws_account_id == "721744297924"
    error_message = "확인된 운영 AWS 계정 721744297924만 허용합니다."
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
    tags = { Project = "seonjalal", Environment = "production", Component = "service-connect", ManagedBy = "terraform" }
  }
}

# BE를 AI보다 먼저 배포할 수 있도록 공용 Namespace의 소유권을 별도 State에 둔다.
resource "aws_service_discovery_http_namespace" "production" {
  name        = "seonjalal-v2-production-services"
  description = "Production ECS Service Connect namespace shared by BE and AI"
}

output "namespace_arn" {
  value = aws_service_discovery_http_namespace.production.arn
}

output "namespace_name" {
  value = aws_service_discovery_http_namespace.production.name
}
