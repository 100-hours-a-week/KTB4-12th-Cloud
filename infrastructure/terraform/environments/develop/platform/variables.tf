variable "aws_account_id" {
  description = "V2 Develop을 구축할 AWS Account ID"
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "aws_account_id must be a 12-digit AWS account ID."
  }
}

variable "aws_region" {
  description = "V2 Develop의 AWS Region"
  type        = string
  default     = "ap-northeast-2"

  validation {
    condition     = var.aws_region == "ap-northeast-2"
    error_message = "V2 Develop의 기준 Region은 ap-northeast-2입니다."
  }
}

variable "aws_profile" {
  description = "Local Plan에서만 사용할 AWS CLI Profile. CI에서는 OIDC Role을 사용한다."
  type        = string
  default     = null
  nullable    = true
}

variable "foundation_state_bucket" {
  description = "Foundation Terraform State S3 Bucket"
  type        = string
}

variable "foundation_state_key" {
  description = "Foundation Terraform State Object Key"
  type        = string
  default     = "v2/develop/foundation/terraform.tfstate"
}

variable "foundation_state_region" {
  description = "Foundation Terraform State S3 Region"
  type        = string
  default     = "ap-northeast-2"
}

variable "foundation_state_profile" {
  description = "Foundation State 조회용 Local AWS Profile. CI에서는 null을 사용한다."
  type        = string
  default     = null
  nullable    = true
}

variable "container_instance_types" {
  description = "애플리케이션 ECS Capacity Provider가 사용할 EC2 Instance Type 우선순위"
  type        = list(string)
  default     = ["t3.large"]

  validation {
    condition     = length(var.container_instance_types) > 0
    error_message = "container_instance_types에는 최소 한 개의 Instance Type이 필요합니다."
  }
}

variable "managed_scaling_enabled" {
  description = "Egress와 Workload 준비 후 활성화할 ECS Managed Scaling. 빈 Platform에서는 false 유지."
  type        = bool
  default     = false
}

variable "asg_min_size" {
  description = "애플리케이션 ECS ASG 최소 Instance 수"
  type        = number
  default     = 0
}

variable "asg_desired_capacity" {
  description = "애플리케이션 ECS ASG 초기 희망 Instance 수"
  type        = number
  default     = 0
}

variable "asg_max_size" {
  description = "애플리케이션 ECS ASG 최대 Instance 수"
  type        = number
  default     = 6
}

variable "root_volume_size_gib" {
  description = "ECS Container Instance Root EBS 크기"
  type        = number
  default     = 50
}

variable "additional_tags" {
  description = "공통 Tag에 추가할 선택 Tag"
  type        = map(string)
  default     = {}
}
