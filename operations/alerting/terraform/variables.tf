variable "aws_account_id" {
  description = "잘못된 계정 적용을 차단할 대상 AWS 계정 ID"
  type        = string

  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "aws_account_id는 12자리 숫자여야 합니다."
  }
}

variable "aws_region" {
  description = "알림 리소스와 감시 대상 EC2가 위치한 AWS 리전"
  type        = string
  default     = "ap-northeast-2"
}

variable "environment" {
  description = "알림 대상 환경 이름"
  type        = string

  validation {
    condition     = contains(["develop", "production"], var.environment)
    error_message = "environment는 develop 또는 production이어야 합니다."
  }
}

variable "ec2_instance_id" {
  description = "CloudWatch Alarm이 감시할 V1 EC2 인스턴스 ID"
  type        = string

  validation {
    condition     = can(regex("^i-[0-9a-f]{8,17}$", var.ec2_instance_id))
    error_message = "유효한 EC2 인스턴스 ID를 입력해야 합니다."
  }
}

variable "discord_webhook_secret_arn" {
  description = "Discord Webhook URL을 저장한 기존 Secrets Manager Secret ARN"
  type        = string
  sensitive   = true

  validation {
    condition     = can(regex("^arn:aws:secretsmanager:", var.discord_webhook_secret_arn))
    error_message = "Secrets Manager Secret ARN을 입력해야 합니다."
  }
}

variable "cpu_threshold_percent" {
  description = "CPU 경고 임계값. 초기값이며 운영 Baseline 이후 조정한다."
  type        = number
  default     = 80

  validation {
    condition     = var.cpu_threshold_percent > 0 && var.cpu_threshold_percent <= 100
    error_message = "CPU 임계값은 0 초과 100 이하여야 합니다."
  }
}

variable "alarm_actions_enabled" {
  description = "false이면 Alarm은 생성하지만 SNS 알림 Action은 연결하지 않는다."
  type        = bool
  default     = false
}
