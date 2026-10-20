variable "enable_source_secret_bootstrap" {
  description = "원본 인증 설정 전달 중에만 true. 완료 후 false로 임시 기록 정책 제거."
  type        = bool
  default     = false
}

variable "source_secret_bootstrap_deadline" {
  description = "임시 기록 권한의 고정 UTC 만료 시각. 활성화 시 Plan 시점부터 최대 4시간."
  type        = string
  default     = ""
  validation {
    condition     = var.source_secret_bootstrap_deadline == "" || can(formatdate("YYYY", var.source_secret_bootstrap_deadline))
    error_message = "UTC RFC3339 만료 시각 필요."
  }
}

data "aws_secretsmanager_secret" "runtime" {
  name = "seonjalal/production/backend/runtime"
}

data "aws_iam_role" "source" {
  count = var.enable_source_secret_bootstrap ? 1 : 0
  name  = "seonjalal-v1-ec2-role"
}

data "aws_security_group" "backend" {
  name   = "seonjalal-v2-production-backend"
  vpc_id = aws_security_group.node.vpc_id
}

data "aws_vpc_endpoint" "secrets" {
  vpc_id       = aws_security_group.node.vpc_id
  service_name = "com.amazonaws.ap-northeast-2.secretsmanager"
}

locals {
  task_trust = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
      Condition = {
        StringEquals = { "aws:SourceAccount" = var.aws_account_id }
        ArnLike      = { "aws:SourceArn" = "arn:aws:ecs:ap-northeast-2:${var.aws_account_id}:*" }
      }
    }]
  })
}

# 기존 V1 역할 전체를 인수/교체하지 않고 한 Secret 전용 임시 정책만 추가.
# Source DB 자격증명은 전달 대상에서 제외. 실제 값은 별도 승인 절차로 기록.
resource "aws_iam_role_policy" "source_runtime_bootstrap" {
  count = var.enable_source_secret_bootstrap ? 1 : 0
  name  = "SeonjalalProductionRuntimeBootstrap"
  role  = data.aws_iam_role.source[0].name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:DescribeSecret", "secretsmanager:PutSecretValue"]
      Resource = data.aws_secretsmanager_secret.runtime.arn
      Condition = {
        StringEquals = { "aws:RequestedRegion" = "ap-northeast-2" }
        DateLessThan = { "aws:CurrentTime" = var.source_secret_bootstrap_deadline }
      }
    }]
  })
  lifecycle {
    precondition {
      condition = (
        try(timecmp(var.source_secret_bootstrap_deadline, plantimestamp()), -1) > 0 &&
        try(timecmp(var.source_secret_bootstrap_deadline, timeadd(plantimestamp(), "4h")), 1) <= 0
      )
      error_message = "임시 기록 권한에는 현재부터 최대 4시간 이내인 고정 만료 시각 필요."
    }
  }
}

# 이미지 취득/로그/Secret 주입은 ECS 실행 역할에서 수행. 앱 역할과 분리.
resource "aws_iam_role" "task_execution" {
  name               = "${local.name}-task-execution"
  assume_role_policy = local.task_trust
}

resource "aws_iam_role_policy_attachment" "task_execution" {
  role       = aws_iam_role.task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "task_runtime_secret" {
  name = "production-runtime-secret"
  role = aws_iam_role.task_execution.name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = data.aws_secretsmanager_secret.runtime.arn
    }]
  })
  lifecycle {
    precondition {
      condition     = data.aws_secretsmanager_secret.runtime.kms_key_id == "" || data.aws_secretsmanager_secret.runtime.kms_key_id == null
      error_message = "사용자 관리 KMS 키가 있으면 정확한 Decrypt 권한을 별도 검토해야 합니다."
    }
  }
}

# 앱 역할의 버킷별 최소 권한은 workload.tf에서 별도 관리.
resource "aws_iam_role" "task" {
  name               = "${local.name}-backend-task"
  assume_role_policy = local.task_trust
}

# ECS Agent가 Private DNS를 통해 Secret을 가져올 때 호스트 SG만 허용.
resource "aws_vpc_security_group_ingress_rule" "secrets_from_node" {
  for_each                     = toset(data.aws_vpc_endpoint.secrets.security_group_ids)
  security_group_id            = each.key
  referenced_security_group_id = aws_security_group.node.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  description                  = "Production ECS agent runtime secret injection"
  lifecycle {
    precondition {
      condition     = data.aws_vpc_endpoint.secrets.state == "available" && data.aws_vpc_endpoint.secrets.private_dns_enabled
      error_message = "Secrets Manager Endpoint의 available/PrivateDNS 확인 필요."
    }
  }
}

# 앱의 S3/외부 HTTPS 연동용. RDS 3306 및 인바운드는 기존 경계 유지.
resource "aws_vpc_security_group_egress_rule" "backend_https" {
  security_group_id = data.aws_security_group.backend.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  description       = "Production backend S3 and external HTTPS through existing NAT"
}
