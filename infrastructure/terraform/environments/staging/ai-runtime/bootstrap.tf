locals {
  bootstrap_name = "seonjalal-v2-staging-ai-db-bootstrap"
  bootstrap_trust = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow", Principal = { Service = "ecs-tasks.amazonaws.com" }, Action = "sts:AssumeRole"
      Condition = {
        StringEquals = { "aws:SourceAccount" = var.aws_account_id }
        ArnLike      = { "aws:SourceArn" = "arn:aws:ecs:ap-northeast-2:${var.aws_account_id}:*" }
      }
    }]
  })
}

data "aws_vpc_endpoint" "secrets" {
  count        = var.deployment != null && var.bootstrap_admin_secret_arn != null ? 1 : 0
  vpc_id       = var.deployment.vpc_id
  service_name = "com.amazonaws.ap-northeast-2.secretsmanager"
}

# Bootstrap Task의 boto3 호출만 허용. ECS Agent의 Secret 주입은 기존 Node SG 경로를 사용한다.
resource "aws_vpc_security_group_ingress_rule" "secrets_from_bootstrap" {
  for_each                     = length(data.aws_vpc_endpoint.secrets) == 0 ? toset([]) : toset(data.aws_vpc_endpoint.secrets[0].security_group_ids)
  security_group_id            = each.key
  referenced_security_group_id = module.ai[0].runtime.task_security_group_id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  description                  = "One-time staging AI DB bootstrap Secret writes"
  lifecycle {
    precondition {
      condition     = data.aws_vpc_endpoint.secrets[0].state == "available" && data.aws_vpc_endpoint.secrets[0].private_dns_enabled
      error_message = "Secrets Manager Endpoint available/Private DNS 필요."
    }
  }
}

# 이 Task Definition은 등록만 한다. 실제 역할·Secret·pgvector 변경은 별도 승인된 run-task에서만 발생한다.
resource "aws_iam_role" "bootstrap_execution" {
  count              = var.deployment != null && var.bootstrap_admin_secret_arn != null ? 1 : 0
  name               = "${local.bootstrap_name}-execution"
  assume_role_policy = local.bootstrap_trust
}

resource "aws_iam_role_policy_attachment" "bootstrap_execution" {
  count      = length(aws_iam_role.bootstrap_execution)
  role       = aws_iam_role.bootstrap_execution[0].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "bootstrap_admin_secret" {
  count = length(aws_iam_role.bootstrap_execution)
  name  = "ReadOnlyStagingAiAdminSecret"
  role  = aws_iam_role.bootstrap_execution[0].name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow", Action = ["secretsmanager:GetSecretValue"], Resource = var.bootstrap_admin_secret_arn
      Condition = { DateLessThan = { "aws:CurrentTime" = var.bootstrap_expires_at } }
    }]
  })
  lifecycle {
    precondition {
      condition = (
        strcontains(var.bootstrap_admin_secret_arn, ":${var.aws_account_id}:") &&
        try(timecmp(var.bootstrap_expires_at, plantimestamp()), -1) > 0 &&
        try(timecmp(var.bootstrap_expires_at, timeadd(plantimestamp(), "4h")), 1) <= 0
      )
      error_message = "Bootstrap Secret 권한은 현재부터 최대 4시간 이내의 UTC 만료 시각 필요."
    }
  }
}

resource "aws_iam_role" "bootstrap_task" {
  count              = length(aws_iam_role.bootstrap_execution)
  name               = "${local.bootstrap_name}-task"
  assume_role_policy = local.bootstrap_trust
}

resource "aws_iam_role_policy" "bootstrap_write" {
  count = length(aws_iam_role.bootstrap_task)
  name  = "InitializeOnlyStagingAiDbSecrets"
  role  = aws_iam_role.bootstrap_task[0].name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = ["secretsmanager:DescribeSecret", "secretsmanager:GetSecretValue", "secretsmanager:PutSecretValue"]
      Resource  = values(module.ai[0].runtime.runtime_secret_arns)
      Condition = { DateLessThan = { "aws:CurrentTime" = var.bootstrap_expires_at } }
    }]
  })
}

resource "aws_ecs_task_definition" "bootstrap" {
  count                    = length(aws_iam_role.bootstrap_task)
  family                   = local.bootstrap_name
  requires_compatibilities = ["EC2"]
  network_mode             = "awsvpc"
  cpu                      = "256"
  memory                   = "1024"
  task_role_arn            = aws_iam_role.bootstrap_task[0].arn
  execution_role_arn       = aws_iam_role.bootstrap_execution[0].arn
  skip_destroy             = true
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }
  container_definitions = jsonencode([{
    name                   = "bootstrap"
    image                  = var.deployment.images.migrate
    essential              = true
    user                   = "10001:10001"
    readonlyRootFilesystem = true
    entryPoint             = ["python", "-c", file("${path.module}/bootstrap.py")]
    command                = []
    memory                 = 768
    linuxParameters        = { tmpfs = [{ containerPath = "/tmp", size = 16, mountOptions = ["rw", "nosuid", "nodev", "uid=10001", "gid=10001", "mode=0700"] }] }
    environment = [for name, value in {
      AI_DATABASE_HOST     = var.deployment.database_host
      AI_DATABASE_NAME     = "ai_db_staging"
      RDS_CA_PEM           = file("${path.module}/../rehearsal/certs/ap-northeast-2-bundle.pem")
      APP_SECRET_ARN       = module.ai[0].runtime.runtime_secret_arns.app
      MIGRATION_SECRET_ARN = module.ai[0].runtime.runtime_secret_arns.migrate
      CATALOG_SECRET_ARN   = module.ai[0].runtime.runtime_secret_arns.catalog
    } : { name = name, value = value }]
    secrets = [
      { name = "ADMIN_USERNAME", valueFrom = "${var.bootstrap_admin_secret_arn}:username::" },
      { name = "ADMIN_PASSWORD", valueFrom = "${var.bootstrap_admin_secret_arn}:password::" },
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = "/ecs/seonjalal-v2-staging-ai"
        awslogs-region        = "ap-northeast-2"
        awslogs-stream-prefix = "ai-db-bootstrap"
      }
    }
  }])
  depends_on = [aws_iam_role_policy.bootstrap_admin_secret, aws_iam_role_policy.bootstrap_write]
}

output "ai_db_bootstrap_task_definition_arn" {
  description = "일회성 수동 실행용. Terraform Apply는 이 Task를 실행하지 않음"
  value       = try(aws_ecs_task_definition.bootstrap[0].arn, null)
}
