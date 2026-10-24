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
    error_message = "스테이징 DB는 서울 리전만 허용."
  }
}

variable "vpc_id" {
  type = string
}

variable "data_subnet_ids" {
  type        = set(string)
  description = "운영·AI DB와 공유하는 기존 Private Data Subnet 2개(서로 다른 AZ)."
  validation {
    condition     = length(var.data_subnet_ids) == 2
    error_message = "서로 다른 두 AZ의 기존 Private Data Subnet 2개 필요."
  }
}

variable "enable_database" {
  type        = bool
  default     = false
  description = "false면 DB를 만들지 않는다. 서브넷 그룹·보안 그룹·파라미터 그룹도 DB와 함께만 생성."
}

variable "snapshot_identifier" {
  type        = string
  default     = null
  description = "복원할 스냅샷 식별자. 데이터 성격 확인 전에는 지정하지 않는다."
  validation {
    condition     = var.snapshot_identifier == null ? true : can(regex("^[A-Za-z][A-Za-z0-9-]{0,254}$", var.snapshot_identifier))
    error_message = "유효한 스냅샷 식별자 필요."
  }
}

variable "snapshot_kms_key_arn" {
  type        = string
  default     = null
  description = "암호화된 스냅샷 복원 시 사용할 KMS 키. null이면 스냅샷의 키를 따른다."
}

variable "database_instance_class" {
  type        = string
  default     = "db.t4g.small"
  description = "운영 MySQL과 같은 사양. 낮추면 부하 시험 수치를 운영 근거로 쓸 수 없다."
  validation {
    condition     = var.database_instance_class == "db.t4g.small"
    error_message = "운영 동일 사양(db.t4g.small)만 허용. 변경하려면 사유와 함께 이 검증을 먼저 수정."
  }
}

variable "database_max_storage_gib" {
  type        = number
  default     = 50
  description = "20GiB 시작, 자동 확장 상한. 확장된 용량은 자동 축소되지 않는다."
  validation {
    condition     = var.database_max_storage_gib >= 22 && var.database_max_storage_gib <= 100 && floor(var.database_max_storage_gib) == var.database_max_storage_gib
    error_message = "자동 확장 상한은 22~100GiB 정수."
  }
}
