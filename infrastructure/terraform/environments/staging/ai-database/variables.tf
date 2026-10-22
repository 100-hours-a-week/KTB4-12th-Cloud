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

variable "aws_region" {
  type    = string
  default = "ap-northeast-2"
  validation {
    condition     = var.aws_region == "ap-northeast-2"
    error_message = "AI 스테이징 DB는 서울 리전만 허용."
  }
}

variable "vpc_id" {
  type = string
}

variable "data_subnet_ids" {
  type        = set(string)
  description = "기존 공유 Private Data Subnet 2개. 실제 DB는 서울 A에만 배치"
  validation {
    condition     = length(var.data_subnet_ids) == 2
    error_message = "DB Subnet Group에 서로 다른 두 AZ의 기존 Private Subnet 필요."
  }
}

variable "database_instance_class" {
  type        = string
  default     = "db.t4g.micro"
  description = "최소 스테이징 시작 사양. 메모리·연결·Migration 실측 후 상향 검토"
  validation {
    condition     = contains(["db.t4g.micro", "db.t4g.small"], var.database_instance_class)
    error_message = "현재 비용·호환성 검토 범위는 db.t4g.micro 또는 db.t4g.small."
  }
}
