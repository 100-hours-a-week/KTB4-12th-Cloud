locals {
  base_environment = {
    AWS_REGION                                  = var.aws_region
    PRODUCT_IMAGE_BUCKET                        = var.config.product_image_bucket
    RDS_CA_BUNDLE                               = var.rds_ca_bundle
    SPRING_FLYWAY_ENABLED                       = "false"
    SPRING_SQL_INIT_MODE                        = "never"
    SPRING_JPA_HIBERNATE_DDL_AUTO               = "validate"
    SPRING_JPA_SHOW_SQL                         = "false"
    SCHEDULING_ENABLED                          = "false"
    AI_PROFILE_SCHEDULING_ENABLED               = "false"
    SERVER_SHUTDOWN                             = "graceful"
    SPRING_LIFECYCLE_TIMEOUT_PER_SHUTDOWN_PHASE = "45s"
    AWS_EC2_METADATA_DISABLED                   = "true"
  }
  main_environment = merge(var.config.main_environment, local.base_environment, {
    # BE가 이 값 뒤에 "/{대화ID}"와 "/{대화ID}/turns/{turnId}/events"를 붙이므로 챗봇 API 경로까지 포함해야 함(끝에 / 금지).
    AI_CHAT_BASE_URL              = "http://ai-chat:7860/v1/chat/conversations"
    AI_PROFILE_BASE_URL           = "http://ai-profiler:8001"
    PAYMENT_SERVER_BASE_URL       = "http://payment-server:8090"
    SCHEDULING_ENABLED            = tostring(var.config.scheduling_enabled)
    AI_PROFILE_SCHEDULING_ENABLED = tostring(var.config.ai_profile_scheduling_enabled)
  })
  payment_environment = merge(var.config.payment_environment, local.base_environment, {
    PAYMENT_SERVER_PORT = "8090"
  })
  containers = {
    main_backend = {
      name         = "main-backend"
      image        = var.config.images.main_backend
      essential    = true
      entryPoint   = ["/bin/sh", "-c"]
      command      = [file("${path.module}/rds-tls-bootstrap.sh")]
      portMappings = [{ name = "main-backend", containerPort = 8080, protocol = "tcp", appProtocol = "http" }]
      environment  = [for key, value in local.main_environment : { name = key, value = value }]
      secrets = [for key in sort(tolist(var.config.main_secret_keys)) : {
        name = key, valueFrom = "${aws_secretsmanager_secret.runtime.arn}:${key}::"
      }]
      healthCheck = {
        command  = ["CMD-SHELL", "curl -fsS --max-time 3 http://127.0.0.1:8080/actuator/health >/dev/null || exit 1"]
        interval = 10, timeout = 5, retries = 3, startPeriod = 90
      }
      stopTimeout = 60
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.service["main_backend"].name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "main-backend"
        }
      }
    }
    payment_server = {
      name         = "payment-server"
      image        = var.config.images.payment_server
      essential    = true
      entryPoint   = ["/bin/sh", "-c"]
      command      = [file("${path.module}/rds-tls-bootstrap.sh")]
      portMappings = [{ name = "payment-server", containerPort = 8090, protocol = "tcp", appProtocol = "http" }]
      environment  = [for key, value in local.payment_environment : { name = key, value = value }]
      secrets = [for key in sort(tolist(var.config.payment_secret_keys)) : {
        name = key, valueFrom = "${aws_secretsmanager_secret.runtime.arn}:${key}::"
      }]
      healthCheck = {
        command  = ["CMD-SHELL", "curl -fsS --max-time 3 http://127.0.0.1:8090/actuator/health >/dev/null || exit 1"]
        interval = 10, timeout = 5, retries = 3, startPeriod = 90
      }
      stopTimeout = 60
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.service["payment_server"].name
          awslogs-region        = var.aws_region
          awslogs-stream-prefix = "payment-server"
        }
      }
    }
  }
}

resource "aws_ecs_task_definition" "service" {
  for_each                 = local.names
  family                   = each.value
  requires_compatibilities = ["EC2"]
  network_mode             = "awsvpc"
  cpu                      = "512"
  memory                   = each.key == "main_backend" ? "1536" : "1024"
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task[each.key].arn
  container_definitions    = jsonencode([local.containers[each.key]])
  skip_destroy             = true
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }
  lifecycle {
    create_before_destroy = true
  }
  depends_on = [terraform_data.guard]
}

resource "aws_lb_target_group" "main" {
  name_prefix          = var.environment == "staging" ? "stgbm-" : "prdbm-"
  port                 = 8080
  protocol             = "HTTP"
  target_type          = "ip"
  vpc_id               = var.config.vpc_id
  deregistration_delay = 60
  health_check {
    path                = "/actuator/health"
    matcher             = "200"
    interval            = 10
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 2
  }
  lifecycle { create_before_destroy = true }
  depends_on = [terraform_data.guard]
}

# ECS CreateService는 Target Group이 Listener에 연결되어 있어야 한다.
# 운영 요청 Host와 일치하지 않는 예약된 .invalid Host로 먼저 연결하고,
# 실제 운영 Host 전환은 main_cutover의 별도 승인 경계로 유지한다.
resource "aws_lb_listener_rule" "main_parking" {
  count        = var.environment == "production" ? 1 : 0
  listener_arn = var.config.alb_listener_arn
  priority     = 8

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.main.arn
  }

  condition {
    host_header { values = ["seonjalal-v2-production-prewarm.invalid"] }
  }

  condition {
    path_pattern { values = ["/__seonjalal-be-prewarm"] }
  }
}

resource "aws_ecs_service" "service" {
  for_each                           = local.names
  name                               = each.value
  cluster                            = var.config.cluster_arn
  task_definition                    = aws_ecs_task_definition.service[each.key].arn
  desired_count                      = var.config.desired_count
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200
  health_check_grace_period_seconds  = each.key == "main_backend" ? 180 : null
  enable_ecs_managed_tags            = true
  propagate_tags                     = "SERVICE"
  deployment_controller { type = "ECS" }
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
  capacity_provider_strategy {
    capacity_provider = var.config.capacity_provider
    base              = 1
    weight            = 1
  }
  network_configuration {
    subnets          = var.config.subnet_ids
    security_groups  = [aws_security_group.task[each.key].id]
    assign_public_ip = false
  }
  dynamic "load_balancer" {
    for_each = each.key == "main_backend" ? [1] : []
    content {
      target_group_arn = aws_lb_target_group.main.arn
      container_name   = "main-backend"
      container_port   = 8080
    }
  }
  service_connect_configuration {
    enabled   = true
    namespace = var.config.namespace_arn
    dynamic "service" {
      for_each = each.key == "payment_server" ? [1] : []
      content {
        port_name = "payment-server"
        client_alias {
          dns_name = "payment-server"
          port     = 8090
        }
      }
    }
  }
  lifecycle {
    # Task Revision은 Cloud CD에서 고정 Digest로 전진. 템플릿 변경 시 배포자가
    # 출력된 최신 template ARN을 기준으로 새 Revision을 등록해야 한다.
    ignore_changes = [task_definition]
  }
  depends_on = [
    aws_iam_role_policy_attachment.execution,
    aws_iam_role_policy.secrets,
    aws_iam_role_policy.product_images,
    aws_vpc_security_group_ingress_rule.database,
    aws_vpc_security_group_ingress_rule.secrets_endpoint,
    aws_vpc_security_group_egress_rule.alb_to_main,
    aws_lb_listener_rule.main_parking,
  ]
}

# 새 Service가 살아난 뒤 별도 승인/Plan에서 Host 규칙을 켜서 이전 기본 Target을 보존한다.
resource "aws_lb_listener_rule" "main_cutover" {
  count        = var.config.traffic_cutover ? 1 : 0
  listener_arn = var.config.alb_listener_arn
  priority     = var.config.listener_rule_priority
  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.main.arn
  }
  condition {
    host_header { values = [var.config.application_domain_name] }
  }
  lifecycle {
    precondition {
      condition = (
        var.config.desired_count >= 2 &&
        var.config.traffic_health_verified &&
        alltrue(values(var.config.activation_checks)) &&
        (var.environment != "production" || var.config.listener_rule_priority == 9)
      )
      error_message = "Main Target Health·승인된 전환 및 운영 Origin Listener 우선순위 9 확인 전 Rule 생성 금지."
    }
  }
}
