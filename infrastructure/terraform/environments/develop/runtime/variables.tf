variable "aws_account_id" {
  description = "Develop Runtime을 생성할 AWS Account ID"
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "aws_account_id must be a 12-digit AWS account ID."
  }
}

variable "aws_region" {
  description = "Develop Runtime의 AWS Region"
  type        = string
  default     = "ap-northeast-2"

  validation {
    condition     = var.aws_region == "ap-northeast-2"
    error_message = "Develop Runtime의 기준 Region은 ap-northeast-2입니다."
  }
}

variable "aws_profile" {
  description = "Local Plan에서 사용할 AWS CLI Profile. CI에서는 null로 두고 OIDC Role을 사용한다."
  type        = string
  default     = null
  nullable    = true
}

variable "foundation_state_bucket" {
  description = "V2 Develop Foundation Remote State가 저장된 S3 Bucket"
  type        = string

  validation {
    condition     = length(trimspace(var.foundation_state_bucket)) > 0
    error_message = "foundation_state_bucket must not be empty."
  }
}

variable "foundation_state_key" {
  description = "V2 Develop Foundation Remote State의 S3 Object Key"
  type        = string
  default     = "v2/develop/foundation/terraform.tfstate"
}

variable "instance_type" {
  description = "FE, BE와 MySQL을 실행할 초기 Develop EC2 Type. AI 추가 전 별도 용량 검증이 필요하다."
  type        = string

  validation {
    condition     = can(regex("^[a-z][a-z0-9]*[0-9]*\\.[a-z0-9]+$", var.instance_type))
    error_message = "instance_type must be a valid EC2 instance type such as t3.medium."
  }
}

variable "root_volume_size_gib" {
  description = "재생성 가능한 Develop Root gp3 Volume 크기"
  type        = number
  default     = 30

  validation {
    condition     = var.root_volume_size_gib >= 20 && var.root_volume_size_gib <= 100
    error_message = "root_volume_size_gib must be between 20 and 100 GiB."
  }
}

variable "product_image_bucket_name" {
  description = "Develop Backend가 Presigned GET URL을 생성할 상품 이미지 S3 Bucket 이름"
  type        = string

  validation {
    condition = (
      length(var.product_image_bucket_name) >= 3 &&
      length(var.product_image_bucket_name) <= 63 &&
      can(regex("^[a-z0-9][a-z0-9.-]*[a-z0-9]$", var.product_image_bucket_name))
    )
    error_message = "product_image_bucket_name must be a valid S3 bucket name."
  }
}

variable "bootstrap_version" {
  description = "Host Bootstrap 변경을 추적하기 위한 Version"
  type        = string
  default     = "2026-10-10.2"
}

variable "initial_frontend_sha" {
  description = "Desired State Parameter를 최초 생성할 때 사용할 검증 완료 FE Git SHA"
  type        = string

  validation {
    condition     = can(regex("^[0-9a-f]{40}$", var.initial_frontend_sha))
    error_message = "initial_frontend_sha must be a 40-character lowercase Git SHA."
  }
}

variable "initial_backend_sha" {
  description = "Desired State Parameter를 최초 생성할 때 사용할 검증 완료 BE Git SHA"
  type        = string

  validation {
    condition     = can(regex("^[0-9a-f]{40}$", var.initial_backend_sha))
    error_message = "initial_backend_sha must be a 40-character lowercase Git SHA."
  }
}

variable "additional_tags" {
  description = "공통 Tag에 추가할 선택 Tag"
  type        = map(string)
  default     = {}
}
