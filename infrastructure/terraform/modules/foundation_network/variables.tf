variable "name_prefix" {
  description = "모든 Foundation Resource에 사용할 이름 Prefix"
  type        = string
}

variable "vpc_cidr" {
  description = "V2 전용 VPC CIDR"
  type        = string
}

variable "availability_zones" {
  description = "Foundation을 배치할 AZ 목록"
  type        = list(string)

  validation {
    condition     = length(var.availability_zones) == 2
    error_message = "V2 Foundation은 정확히 두 AZ를 사용합니다."
  }
}

variable "subnet_cidrs" {
  description = "Public, App, Data 계층별 AZ CIDR"
  type = object({
    public = map(string)
    app    = map(string)
    data   = map(string)
  })
}

variable "application_port" {
  description = "ALB가 ECS Task로 전달할 Backend Port"
  type        = number
  default     = 8080
}
