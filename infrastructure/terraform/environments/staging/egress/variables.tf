variable "aws_account_id" {
  type = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "12자리계정ID필요."
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
    error_message = "서울리전리허설전용."
  }
}
variable "vpc_id" {
  type        = string
  description = "기존리허설VPC. VPC자체변경없음"
}
variable "nat_public_subnet_id" {
  type        = string
  description = "IGW기본경로가검증된기존AZ a Public Subnet"
}
variable "enable_nat_instance" {
  type        = bool
  default     = false
  description = "NAT EC2/EIP/IAM 생성. EC2/EBS/Public IPv4 과금 발생"
}
variable "app_subnet_ids" {
  type        = map(string)
  description = "기존 App Subnet a/c 재사용. 새 Subnet 생성 없음"
  validation {
    condition     = toset(keys(var.app_subnet_ids)) == toset(["a", "c"]) && length(toset(values(var.app_subnet_ids))) == 2
    error_message = "서로 다른 App Subnet a/c 필요."
  }
}
variable "attach_app_routes" {
  type        = bool
  default     = false
  description = "NAT 기동 확인 후 별도 Plan으로 기존 App 기본 Route 2개 추가"
  validation {
    condition     = !var.attach_app_routes || var.enable_nat_instance
    error_message = "App Route 연결에는 NAT Instance 활성화 필요."
  }
}
variable "additional_tags" {
  type    = map(string)
  default = {}
}
