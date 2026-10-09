variable "aws_account_id" {
  description = "V2 인프라를 관리할 AWS Account ID"
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "aws_account_id must be a 12-digit AWS account ID."
  }
}

variable "aws_region" {
  description = "Terraform State Bucket을 둘 AWS Region"
  type        = string
  default     = "ap-northeast-2"

  validation {
    condition     = var.aws_region == "ap-northeast-2"
    error_message = "V2의 기준 Region은 ap-northeast-2입니다."
  }
}

variable "aws_profile" {
  description = "Local Bootstrap에서 사용할 AWS CLI Profile. CI에서는 null을 사용하고 OIDC Role을 쓴다."
  type        = string
  default     = null
  nullable    = true
}

variable "project_name" {
  description = "Resource 이름과 Tag에 사용할 Project 이름"
  type        = string
  default     = "seonjalal"
}

variable "state_bucket_name" {
  description = "전역에서 고유해야 하는 State Bucket 이름. null이면 Account와 Region으로 계산한다."
  type        = string
  default     = null
  nullable    = true
}

variable "additional_tags" {
  description = "공통 Tag에 추가할 선택 Tag"
  type        = map(string)
  default     = {}
}
