# 앱 대신 짧은 명령만 실행하는 별도 Task 정의. 등록만으로 Task가 실행되지는 않음.
resource "aws_ecs_task_definition" "runtime_probe" {
  count                    = var.enable_runtime_probe ? 1 : 0
  family                   = "${local.name_prefix}-runtime-probe"
  requires_compatibilities = ["EC2"]
  network_mode             = "awsvpc"
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.task.arn
  container_definitions = jsonencode([{
    name       = "runtime-probe"
    image      = var.backend_image
    essential  = true
    entryPoint = ["/bin/sh", "-c"]
    # 값 자체/전체 환경은 출력하지 않음. 백엔드 main/JAR 및 DB 연결 실행 없음.
    command     = ["set -eu; test -n \"$DB_PASSWORD\"; test -n \"$JWT_SECRET_BASE64\"; test -n \"$LOGIN_RATE_LIMIT_HMAC_SECRET_BASE64\"; test -n \"$PROFILING_SERVICE_TOKEN\"; printf 'RUNTIME_SECRETS_PRESENT=4\\n'; java -version; printf 'RUNTIME_PROBE_OK\\n'"]
    secrets     = local.container.secrets
    stopTimeout = 30
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.backend.name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "runtime-probe"
      }
    }
  }])
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }
  lifecycle {
    precondition {
      # 등록된 정의 보존은 Task 실행이 아님. 실제 RunTask는 별도 승인 절차.
      # 앱 단계 전환 때문에 완료한 점검 정의를 삭제/재등록하지 않음.
      condition     = var.egress_probe_mode || var.private_egress_verified
      error_message = "점검 정의 등록은 Egress 점검 또는 Egress 확인 후 허용."
    }
    precondition {
      condition     = var.artifact_platform_verified && startswith(var.backend_image, "${aws_ecr_repository.backend.repository_url}@sha256:") && can(regex("@sha256:[0-9a-f]{64}$", var.backend_image))
      error_message = "검증된 스테이징 ECR의 amd64 고정 Digest 필요."
    }
    precondition {
      condition     = toset(keys(var.container_secrets)) == toset(["DB_PASSWORD", "JWT_SECRET_BASE64", "LOGIN_RATE_LIMIT_HMAC_SECRET_BASE64", "PROFILING_SERVICE_TOKEN"]) && alltrue([for ref in values(var.container_secrets) : ref.secret_arn == aws_secretsmanager_secret.backend_runtime.arn])
      error_message = "점검은 전용 런타임 Secret의 키4개만 허용. 관리자/DMS Secret 금지."
    }
  }
  depends_on = [aws_iam_role_policy.task_secrets]
}
