# Incident recovery only: registering this definition does not run it.
# The single approved run-task rotates the exposed ai_migrator password and Secret.
resource "aws_ecs_task_definition" "migration_rotation" {
  count                    = length(aws_iam_role.bootstrap_task)
  family                   = "seonjalal-v2-production-ai-db-migration-rotate"
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
    name                   = "rotate"
    image                  = var.deployment.images.migrate
    essential              = true
    user                   = "10001:10001"
    readonlyRootFilesystem = true
    entryPoint             = ["python", "-c", file("${path.module}/rotate_migration.py")]
    command                = []
    memory                 = 768
    linuxParameters        = { tmpfs = [{ containerPath = "/tmp", size = 16, mountOptions = ["rw", "nosuid", "nodev", "uid=10001", "gid=10001", "mode=0700"] }] }
    environment = [for name, value in {
      AI_DATABASE_HOST     = var.deployment.database_host
      AI_DATABASE_NAME     = "ai_db_production"
      RDS_CA_PEM           = file("${path.module}/../../staging/rehearsal/certs/ap-northeast-2-bundle.pem")
      MIGRATION_SECRET_ARN = module.ai[0].runtime.runtime_secret_arns.migrate
    } : { name = name, value = value }]
    secrets = [
      { name = "ADMIN_USERNAME", valueFrom = "${var.bootstrap_admin_secret_arn}:username::" },
      { name = "ADMIN_PASSWORD", valueFrom = "${var.bootstrap_admin_secret_arn}:password::" },
    ]
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = "/ecs/seonjalal-v2-production-ai"
        awslogs-region        = "ap-northeast-2"
        awslogs-stream-prefix = "ai-db-rotation"
      }
    }
  }])
  depends_on = [aws_iam_role_policy.bootstrap_admin_secret, aws_iam_role_policy.bootstrap_write]
}

output "ai_migration_rotation_task_definition_arn" {
  description = "노출된 마이그레이션 자격증명의 일회성 복구용. Terraform Apply는 Task를 실행하지 않음"
  value       = try(aws_ecs_task_definition.migration_rotation[0].arn, null)
}
