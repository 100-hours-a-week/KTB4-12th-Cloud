locals {
  name_prefix = "seonjalal-v2-staging"
  required_tags = {
    Project     = "seonjalal"
    Environment = "staging"
    Version     = "v2"
    Component   = "rehearsal"
    ManagedBy   = "terraform"
    Purpose     = "deployment-rehearsal"
  }
  secret_arns = distinct(values(var.container_secrets)[*].secret_arn)
  container = {
    name      = "backend"
    image     = var.backend_image
    essential = true
    # 이미지 내용은 그대로 유지. 준비 완료 후 exec로 JVM에 종료 신호 직접 전달.
    entryPoint = ["/bin/sh", "-c"]
    # 실패 후보는 TLS/JVM/DB 작업 없이 종료. 정상값 false는 기존 실행문 그대로 유지.
    command      = [var.inject_startup_failure ? "printf 'STAGING_INJECTED_STARTUP_FAILURE_EXIT_78\\n'; exit 78" : file("${path.module}/tls-bootstrap.sh")]
    portMappings = [{ containerPort = 8080, protocol = "tcp" }]
    environment = [for name, value in merge(var.container_environment, {
      SPRING_FLYWAY_ENABLED                       = "false"
      STAGING_RDS_CA_BUNDLE                       = file("${path.module}/certs/ap-northeast-2-bundle.pem")
      SPRING_SQL_INIT_MODE                        = "never"
      SPRING_JPA_HIBERNATE_DDL_AUTO               = "validate"
      SCHEDULING_ENABLED                          = "false"
      AI_PROFILE_SCHEDULING_ENABLED               = "false"
      SERVER_SHUTDOWN                             = "graceful"
      SPRING_LIFECYCLE_TIMEOUT_PER_SHUTDOWN_PHASE = "45s"
    }) : { name = name, value = value }]
    secrets = [for name, reference in var.container_secrets : {
      name      = name
      valueFrom = "${reference.secret_arn}:${reference.json_key}::"
    }]
    healthCheck = {
      command     = ["CMD-SHELL", "curl -fsS http://127.0.0.1:8080/actuator/health > /dev/null || exit 1"]
      interval    = 10
      timeout     = 5
      retries     = 3
      startPeriod = 60
    }
    stopTimeout = 60
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = aws_cloudwatch_log_group.backend.name
        awslogs-region        = var.aws_region
        awslogs-stream-prefix = "backend"
      }
    }
  }
}
