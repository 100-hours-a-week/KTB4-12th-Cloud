# 저장 공간만 관리. Secret 값은 별도 보호 절차로 입력하고 State에 저장하지 않음.
resource "aws_secretsmanager_secret" "backend_runtime" {
  name                    = "seonjalal/staging/backend/runtime"
  description             = "Staging backend runtime credentials; values managed outside Terraform"
  recovery_window_in_days = 7
}

resource "aws_ecr_repository" "backend" {
  name                 = "seonjalal/staging/backend"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = false
  image_scanning_configuration {
    scan_on_push = true
  }
}

resource "aws_cloudwatch_log_group" "backend" {
  name              = "/ecs/${local.name_prefix}/backend"
  retention_in_days = 7
}

resource "aws_iam_role" "task_execution" {
  name = "${local.name_prefix}-task-execution"
  assume_role_policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Principal = { Service = "ecs-tasks.amazonaws.com" }, Action = "sts:AssumeRole" }]
  })
}
resource "aws_iam_role_policy_attachment" "task_execution" {
  role       = aws_iam_role.task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}
resource "aws_iam_role_policy" "task_secrets" {
  count = length(local.secret_arns) > 0 ? 1 : 0
  name  = "runtime-secrets"
  role  = aws_iam_role.task_execution.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = concat([
      { Effect = "Allow", Action = ["secretsmanager:GetSecretValue"], Resource = local.secret_arns }
      ], length(var.secret_kms_key_arns) == 0 ? [] : [
      { Effect = "Allow", Action = ["kms:Decrypt"], Resource = sort(tolist(var.secret_kms_key_arns)) }
    ])
  })
}
resource "aws_iam_role" "task" {
  name               = "${local.name_prefix}-backend-task"
  assume_role_policy = aws_iam_role.task_execution.assume_role_policy
}
resource "aws_iam_role_policy" "product_images" {
  count = var.product_image_bucket_arn == null ? 0 : 1
  name  = "product-images"
  role  = aws_iam_role.task.id
  policy = jsonencode({
    Version = "2012-10-17"
    # 기존 개발 이미지 참조는 조회만 허용. 업로드/삭제 시험은 별도 버킷과 승인 필요.
    Statement = [{ Effect = "Allow", Action = ["s3:GetObject"], Resource = ["${var.product_image_bucket_arn}/*"] }]
  })
}

resource "aws_ecs_task_definition" "backend" {
  count                    = var.enable_service ? 1 : 0
  family                   = "${local.name_prefix}-backend"
  requires_compatibilities = ["EC2"]
  network_mode             = "awsvpc"
  cpu                      = tostring(var.task_cpu)
  memory                   = tostring(var.task_memory_mib)
  execution_role_arn       = aws_iam_role.task_execution.arn
  task_role_arn            = aws_iam_role.task.arn
  container_definitions    = jsonencode([local.container])
  # 기존 Revision도 ACTIVE로 남겨 수동 롤백 대상을 보존한다.
  # 최초 도입 시 앱 설정 교체와 분리하여 이 값을 기존 State에 먼저 반영한다.
  skip_destroy = true
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }
  lifecycle {
    create_before_destroy = true
    precondition {
      condition     = var.rds_tls_bootstrap_verified && can(regex("[?&]sslMode=VERIFY_IDENTITY(&|$)", lookup(var.container_environment, "DB_URL", ""))) && length(regexall("[?&]sslMode=", lookup(var.container_environment, "DB_URL", ""))) == 1 && length(setintersection(toset(concat(keys(var.container_environment), keys(var.container_secrets))), toset(["STAGING_TLS_PREPARE_ONLY", "STAGING_RDS_CA_FILE", "JAVA_TOOL_OPTIONS", "JDK_JAVA_OPTIONS", "_JAVA_OPTIONS"]))) == 0
      error_message = "고정 이미지의 CA 기동 검사 및 JDBC VERIFY_IDENTITY 필요. 앱 실행을 생략하거나 JVM 신뢰 설정을 우회하는 입력 금지."
    }
    precondition {
      condition     = var.secrets_endpoint_security_group_id != null && var.database_security_group_id != null
      error_message = "PrivateDNS Secrets Endpoint와리허설RDS의스테이징접속SG경계확인필요."
    }
    precondition {
      condition     = var.artifact_platform_verified && startswith(var.backend_image, "${aws_ecr_repository.backend.repository_url}@sha256:") && can(regex("@sha256:[0-9a-f]{64}$", var.backend_image))
      error_message = "검증한 linux/amd64 이미지를 스테이징 ECR에 확보하고 Digest로 지정해야 합니다."
    }
    precondition {
      condition     = (var.database_cutover_verified != var.database_fixture_verified) && length(lookup(var.container_environment, "DB_URL", "")) > 0
      error_message = "검증된 최신 Cutover 또는 독립 Fixture 중 한 경계와 비밀 없는 DB_URL 확인 필요. 둘 다 미확인/동시 지정 금지."
    }
    precondition {
      condition     = alltrue([for key in ["DB_URL", "DB_USERNAME", "DB_PASSWORD", "JWT_ISSUER", "JWT_SECRET_BASE64", "LOGIN_RATE_LIMIT_HMAC_SECRET_BASE64", "CORS_ALLOWED_ORIGINS", "PRODUCT_IMAGE_BUCKET", "AI_PROFILE_BASE_URL", "PROFILING_SERVICE_TOKEN", "AWS_REGION"] : contains(concat(keys(var.container_environment), keys(var.container_secrets)), key)])
      error_message = "BE 1.3.0 실행에 필요한 환경변수/Secret 참조 누락."
    }
    precondition {
      condition     = length(setintersection(toset(keys(var.container_environment)), toset(keys(var.container_secrets)))) == 0
      error_message = "평문 환경변수와 Secret 참조에 같은 이름 중복 금지."
    }
  }
}

resource "aws_ecs_service" "backend" {
  count                              = var.enable_service ? 1 : 0
  name                               = "${local.name_prefix}-backend"
  cluster                            = aws_ecs_cluster.this.id
  task_definition                    = aws_ecs_task_definition.backend[0].arn
  desired_count                      = var.desired_task_count
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200
  health_check_grace_period_seconds  = 120
  enable_ecs_managed_tags            = true
  propagate_tags                     = "SERVICE"
  # 현재는 이 Root가 desired/task_definition을 소유한다. Cloud CD로 소유권을 넘기기 전
  # ignore_changes를 추가하지 않으며, 별도 변경에서 배포 스크립트와 함께 전환한다.
  deployment_controller {
    type = "ECS"
  }
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
  capacity_provider_strategy {
    capacity_provider = aws_ecs_capacity_provider.application.name
    weight            = 1
    base              = 1
  }
  network_configuration {
    subnets          = var.app_subnet_ids
    security_groups  = [aws_security_group.task.id]
    assign_public_ip = false
  }
  dynamic "load_balancer" {
    for_each = var.enable_alb ? [1] : []
    content {
      target_group_arn = aws_lb_target_group.backend[0].arn
      container_name   = "backend"
      container_port   = 8080
    }
  }
  ordered_placement_strategy {
    type  = "spread"
    field = "attribute:ecs.availability-zone"
  }
  ordered_placement_strategy {
    type  = "binpack"
    field = "memory"
  }
  depends_on = [aws_lb_listener.https, aws_ecs_cluster_capacity_providers.this,
    aws_iam_role_policy_attachment.task_execution, aws_iam_role_policy.task_secrets,
  aws_iam_role_policy.product_images]
  lifecycle {
    precondition {
      condition = var.desired_task_count == 0 ? (
        var.asg_min_size == 0 &&
        var.asg_desired_capacity == 0 &&
        !var.managed_scaling_enabled
        ) : (
        var.enable_alb &&
        var.private_egress_verified &&
        var.asg_min_size >= 2 &&
        var.asg_desired_capacity >= 2
      )
      error_message = "휴면 Service는 task0·ASG min/desired0·ManagedScaling 비활성, 활성 Service는 ALB/Egress 검증과 최소 EC2/Task2개 필요."
    }
  }
}
