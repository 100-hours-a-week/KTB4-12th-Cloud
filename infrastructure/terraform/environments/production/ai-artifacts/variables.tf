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
    error_message = "AI 운영 ECR은 서울 리전만 허용."
  }
}
