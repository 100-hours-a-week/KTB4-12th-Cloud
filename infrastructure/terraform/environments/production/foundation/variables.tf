variable "aws_account_id" {
  type = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "확인한 운영 계정 ID 12자리 필요."
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
    error_message = "현재 운영 전환은 서울 리전만 허용."
  }
}
variable "vpc_id" {
  type = string
}
variable "data_subnet_ids" {
  type = set(string)
  validation {
    condition     = length(var.data_subnet_ids) == 2
    error_message = "서로 다른 두 AZ의 Data Subnet ID 필요."
  }
}

variable "enable_database" {
  type        = bool
  default     = false
  description = "별도 저장 Plan 검토 및 승인 후 운영 빈 RDS 생성. 데이터 이전·전환과 분리"
}
variable "database_instance_class" {
  type        = string
  default     = "db.t4g.small"
  description = "기존 리허설 검증 사양의 시작값. 운영 처리량 적합성 미확정"
  validation {
    condition     = contains(["db.t4g.small", "db.t4g.medium", "db.t4g.large"], var.database_instance_class)
    error_message = "검토 범위는 t4g small/medium/large. 다른 사양은 별도 비용·호환성 검토 필요."
  }
}
variable "database_max_storage_gib" {
  type        = number
  default     = 100
  description = "20GiB 시작, 자동 확장 상한. 확장된 용량은 자동 축소되지 않으므로 비용 검토 필요"
  validation {
    condition     = var.database_max_storage_gib >= 22 && var.database_max_storage_gib <= 100 && floor(var.database_max_storage_gib) == var.database_max_storage_gib
    error_message = "자동 확장 상한은 초기20GiB보다 최소10% 큰 정수22~100GiB만 허용."
  }
}
