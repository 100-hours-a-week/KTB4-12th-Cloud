data "aws_cloudwatch_log_group" "backend" {
  name = "/ecs/seonjalal-v2-production/backend"
}

data "aws_lb_target_group" "backend" {
  arn = "arn:aws:elasticloadbalancing:ap-northeast-2:${var.aws_account_id}:targetgroup/prdbe-d3793c163775d7f56c64cd5b37/49e6eff6eab39c5b"
}

locals {
  # 사용자 실제 pull 및 Image ID/amd64 검사 완료한 기존 V1 Artifact.
  backend_image = "peterj0117/seonjalal-be@sha256:c4dab3ba4e0745d8eff84a15721510037d8c229ad276bacbf8f32168bc6566f2"
  # 조회 전용 DB 접속 정보까지 검증한 버전 고정. Secret 원문은 State에 저장하지 않음.
  runtime_secret_version = "f05cc033-6a98-4d1f-84f2-65f20305766e"
  runtime_secret_keys = toset([
    "AWS_REGION", "CORS_ALLOWED_ORIGINS", "PRODUCT_IMAGE_BUCKET",
    "JWT_ISSUER", "JWT_SECRET_BASE64", "LOGIN_RATE_LIMIT_HMAC_SECRET_BASE64",
    "AI_PROFILE_BASE_URL", "PROFILING_SERVICE_TOKEN", "DISCORD_WEBHOOK_URL",
    "GOOGLE_SERVICE_ACCOUNT_JSON_BASE64", "GOOGLE_SHEET_ID", "GOOGLE_SHEET_TAB",
    "DB_URL", "DB_USERNAME", "DB_PASSWORD",
  ])
}

# 초기 후보 검증은 조회 전용. 상품 이미지 쓰기 권한은 운영 전환 전 별도 검토.
resource "aws_iam_role_policy" "product_images_read" {
  name = "production-product-images-read"
  role = aws_iam_role.task.name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["s3:GetObject"]
      Resource = ["arn:aws:s3:::gift-dev-product-images-${var.aws_account_id}-ap-northeast-2-an/*"]
    }]
  })
}

resource "aws_ecs_task_definition" "backend" {
  family                   = "${local.name}-backend"
  requires_compatibilities = ["EC2"]
  network_mode             = "awsvpc"
  cpu                      = "1024"
  memory                   = "1024"
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.task.arn
  skip_destroy             = true
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }
  container_definitions = jsonencode([{
    name         = "backend"
    image        = local.backend_image
    essential    = true
    entryPoint   = ["/bin/sh", "-c"]
    command      = [file("${path.module}/rds-tls-bootstrap.sh")]
    portMappings = [{ containerPort = 8080, protocol = "tcp" }]
    environment = [for key, value in {
      # 기존 공개 CA 파일만 재사용. Staging State/Secret/DB 참조 없음.
      RDS_CA_BUNDLE                               = file("${path.module}/../../staging/rehearsal/certs/ap-northeast-2-bundle.pem")
      SPRING_FLYWAY_ENABLED                       = "false"
      SPRING_SQL_INIT_MODE                        = "never"
      SPRING_JPA_HIBERNATE_DDL_AUTO               = "validate"
      SPRING_JPA_SHOW_SQL                         = "false"
      SCHEDULING_ENABLED                          = "false"
      AI_PROFILE_SCHEDULING_ENABLED               = "false"
      SERVER_SHUTDOWN                             = "graceful"
      SPRING_LIFECYCLE_TIMEOUT_PER_SHUTDOWN_PHASE = "45s"
      AWS_EC2_METADATA_DISABLED                   = "true"
    } : { name = key, value = value }]
    secrets = [for key in sort(tolist(local.runtime_secret_keys)) : {
      name      = key
      valueFrom = "${data.aws_secretsmanager_secret.runtime.arn}:${key}::${local.runtime_secret_version}"
    }]
    healthCheck = {
      command     = ["CMD-SHELL", "curl -fsS --max-time 3 http://127.0.0.1:8080/actuator/health >/dev/null || exit 1"]
      interval    = 10
      timeout     = 5
      retries     = 3
      startPeriod = 60
    }
    stopTimeout = 60
    logConfiguration = {
      logDriver = "awslogs"
      options = {
        awslogs-group         = data.aws_cloudwatch_log_group.backend.name
        awslogs-region        = "ap-northeast-2"
        awslogs-stream-prefix = "backend"
      }
    }
  }])
  lifecycle {
    create_before_destroy = true
  }
}

# 외부 경로 전환 없이 운영 TG에 후보1개만 등록. DB는 SELECT 계정 유지.
resource "aws_ecs_service" "backend" {
  name                               = "${local.name}-backend"
  cluster                            = data.aws_ecs_cluster.production.arn
  task_definition                    = aws_ecs_task_definition.backend.arn
  desired_count                      = 1
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200
  health_check_grace_period_seconds  = 180
  enable_ecs_managed_tags            = true
  propagate_tags                     = "SERVICE"
  deployment_controller {
    type = "ECS"
  }
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
  capacity_provider_strategy {
    capacity_provider = aws_ecs_capacity_provider.production.name
    base              = 1
    weight            = 1
  }
  network_configuration {
    subnets          = sort(tolist(local.app_subnets))
    security_groups  = [data.aws_security_group.backend.id]
    assign_public_ip = false
  }
  load_balancer {
    target_group_arn = data.aws_lb_target_group.backend.arn
    container_name   = "backend"
    container_port   = 8080
  }
  depends_on = [
    aws_ecs_cluster_capacity_providers.production,
    aws_iam_role_policy_attachment.task_execution,
    aws_iam_role_policy.task_runtime_secret,
    aws_iam_role_policy.product_images_read,
    aws_vpc_security_group_ingress_rule.secrets_from_node,
    aws_vpc_security_group_egress_rule.backend_https,
  ]
  lifecycle {
    precondition {
      condition = (
        data.aws_lb_target_group.backend.target_type == "ip" &&
        data.aws_lb_target_group.backend.port == 8080 &&
        data.aws_lb_target_group.backend.vpc_id == aws_security_group.node.vpc_id
      )
      error_message = "공유 ALB의 운영 전용 IP Target Group 및 VPC/포트 확인 필요."
    }
  }
}
