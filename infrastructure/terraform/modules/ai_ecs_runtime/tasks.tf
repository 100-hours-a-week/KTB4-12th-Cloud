locals {
  # 운영 t3.medium의 실제 ECS 잔여 MEMORY는 3327MiB(2026-10-21 조회).
  # Task 3200MiB로 Host 배치 여유 127MiB를, 컨테이너 Hard Limit 합계
  # 3008MiB로 Service Connect Proxy 몫 192MiB를 남긴 최초 기동용 상한이다.
  # 이 값은 실측 RSS 보장이 아니며 최초 기동 후 OOM·Health를 관측한다.
  app_memory = var.environment == "production" ? {
    task       = 3200
    asset_init = 192
    search     = 1280
    profiler   = 640
    chat       = 896
    } : {
    task       = 4096
    asset_init = 256
    search     = 1536
    profiler   = 1024
    chat       = 1024
  }
  common_environment = {
    AI_DATABASE_NAME        = "ai_db_${var.environment}"
    AI_DATABASE_HOST        = var.config.database_host
    AI_ENVIRONMENT          = var.environment
    AI_CREDENTIAL_BACKEND   = "files"
    AI_CREDENTIAL_LAYOUT    = "separate"
    AWS_REGION              = var.aws_region
    AWS_DEFAULT_REGION      = var.aws_region
    ASSET_ROOT              = "/shared/assets"
    PGSSLMODE               = "verify-full"
    PGSSLROOTCERT           = "/shared/tls/rds-ca.pem"
    PYTHONDONTWRITEBYTECODE = "1"
  }
  linux_parameters = {
    initProcessEnabled = true
    capabilities       = { drop = ["ALL"] }
    tmpfs = [
      { containerPath = "/tmp", size = 64, mountOptions = ["rw", "nosuid", "nodev", "mode=1777"] },
      { containerPath = "/run/runtime", size = 16, mountOptions = ["rw", "nosuid", "nodev", "uid=10001", "gid=10001", "mode=0700"] },
    ]
  }
  logging = {
    logDriver = "awslogs"
    options = {
      awslogs-group         = aws_cloudwatch_log_group.ai.name
      awslogs-region        = var.aws_region
      awslogs-stream-prefix = "ai"
    }
  }
  app_specs = {
    search = {
      port    = 8000, memory = local.app_memory.search
      secrets = ["AI_INTERNAL_SERVICE_TOKEN", "SEARCH_CATALOG_DATABASE_URL"]
      environment = {
        SEARCH_RUNTIME_ROOT = "/runtime"
        SEARCH_MEDIA_ROOT   = "/shared/media"
      }
    }
    profiler = {
      port    = 8001, memory = local.app_memory.profiler
      secrets = ["AI_INTERNAL_SERVICE_TOKEN", "BE_SERVICE_TOKEN", "PROFILING_DATABASE_URL", "OPENROUTER_API_KEY_PROFILER"]
      environment = {
        AI_BUDGET_MODE             = "provider-key-limits"
        PROFILING_BACKEND_BASE_URL = var.config.backend_base_url
        PROFILING_APP_DB_ROLE      = "ai_profiler_service"
        PROFILING_TAGGER_MODEL     = var.config.provider_models.profiler
        SEARCH_RUNTIME_ROOT        = "/runtime"
      }
    }
    chat = {
      port    = 7860, memory = local.app_memory.chat
      secrets = ["AI_INTERNAL_SERVICE_TOKEN", "BE_SERVICE_TOKEN", "DATABASE_URL", "OPENROUTER_API_KEY_CHAT", "SESSION_SECRET", "QA_ADMIN_PASSWORD", "DEMO_ACCESS_TOKEN"]
      environment = {
        AI_BUDGET_MODE  = "provider-key-limits"
        CHAT_MODEL      = var.config.provider_models.chat
        SEARCH_URL      = "http://127.0.0.1:8000"
        INTERNAL_URL    = "http://127.0.0.1:7860"
        CHAT_MEDIA_ROOT = "/shared/media"
        COOKIE_SECURE   = "true"
      }
    }
  }
  asset_init = {
    name                   = "assets-init"
    image                  = var.config.images.migrate
    essential              = false
    user                   = "0:0"
    memory                 = local.app_memory.asset_init
    startTimeout           = 120
    readonlyRootFilesystem = true
    linuxParameters        = merge(local.linux_parameters, { capabilities = { drop = ["ALL"], add = ["CHOWN"] } })
    entryPoint             = ["python", "-c", file("${path.module}/prepare_assets.py")]
    command                = []
    environment = [for name, value in {
      AWS_REGION            = var.aws_region
      ASSET_BUCKET          = var.config.asset_bucket
      ASSET_PREFIX          = var.config.asset_prefix
      ASSET_MANIFEST_KEY    = var.config.asset_manifest_key
      ASSET_MANIFEST_SHA256 = var.config.asset_manifest_sha256
      RDS_CA_PEM            = var.rds_ca_bundle
    } : { name = name, value = value }]
    mountPoints = [
      { sourceVolume = "shared", containerPath = "/shared", readOnly = false },
      { sourceVolume = "search-runtime", containerPath = "/runtime", readOnly = false },
    ]
    logConfiguration = local.logging
  }
  app_containers = [for role, spec in local.app_specs : {
    name                   = role
    image                  = var.config.images[role]
    essential              = true
    user                   = "10001:10001"
    memory                 = spec.memory
    readonlyRootFilesystem = true
    linuxParameters        = local.linux_parameters
    entryPoint             = ["python", "-c", file("${path.module}/launch.py")]
    command                = [role]
    portMappings           = [{ name = "ai-${role}", containerPort = spec.port, protocol = "tcp", appProtocol = "http" }]
    dependsOn = concat(
      [{ containerName = "assets-init", condition = "SUCCESS" }],
      role == "search" ? [] : [{ containerName = "search", condition = "HEALTHY" }]
    )
    environment = [for name, value in merge(local.common_environment, spec.environment, {
      PORT = tostring(spec.port)
    }) : { name = name, value = value }]
    secrets = [for name in spec.secrets : {
      name = name, valueFrom = "${aws_secretsmanager_secret.runtime["app"].arn}:${name}::"
    }]
    mountPoints = concat(
      [{ sourceVolume = "shared", containerPath = "/shared", readOnly = true }],
      role == "chat" ? [] : [{ sourceVolume = "search-runtime", containerPath = "/runtime", readOnly = role == "profiler" }]
    )
    healthCheck = {
      command     = ["CMD", "python", "-m", "integration.containers.health", role]
      interval    = 15
      timeout     = 5
      retries     = 4
      startPeriod = 90
    }
    startTimeout     = 120
    stopTimeout      = 120
    logConfiguration = local.logging
  }]
}

resource "aws_ecs_task_definition" "app" {
  family                   = local.name
  requires_compatibilities = ["EC2"]
  network_mode             = "awsvpc"
  cpu                      = "2048"
  memory                   = tostring(local.app_memory.task)
  task_role_arn            = aws_iam_role.task["app"].arn
  execution_role_arn       = aws_iam_role.execution["app"].arn
  skip_destroy             = true
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }
  volume { name = "shared" }
  volume { name = "search-runtime" }
  container_definitions = jsonencode(concat([local.asset_init], local.app_containers))
  depends_on            = [terraform_data.guard, aws_iam_role_policy.secret_injection, aws_iam_role_policy.assets]
}

locals {
  jobs = {
    migrate = {
      image       = var.config.images.migrate
      secret_keys = ["MIGRATION_DATABASE_URL"]
      environment = {}
    }
    catalog = {
      image       = var.config.images.search
      secret_keys = ["CATALOG_SYNC_DATABASE_URL", "BACKEND_SERVICE_TOKEN"]
      environment = {
        BACKEND_EXPORT_URL                = var.config.backend_export_url
        BACKEND_CATEGORIES_URL            = var.config.backend_categories_url
        BACKEND_EXPORT_ALLOW_PRIVATE_HTTP = "0"
        SEARCH_RUNTIME_ROOT               = "/runtime"
      }
    }
  }
  ca_init = merge(local.asset_init, {
    name        = "ca-init"
    entryPoint  = ["python", "-c", "import os; from pathlib import Path; p=Path('/shared/tls'); p.mkdir(parents=True,exist_ok=True); (p/'rds-ca.pem').write_text(os.environ['RDS_CA_PEM'])"]
    environment = [{ name = "RDS_CA_PEM", value = var.rds_ca_bundle }]
  })
}

resource "aws_ecs_task_definition" "job" {
  for_each                 = local.jobs
  family                   = "${local.name}-${each.key}"
  requires_compatibilities = ["EC2"]
  network_mode             = "awsvpc"
  cpu                      = "1024"
  memory                   = "2048"
  task_role_arn            = aws_iam_role.task[each.key].arn
  execution_role_arn       = aws_iam_role.execution[each.key].arn
  skip_destroy             = true
  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }
  volume { name = "shared" }
  volume { name = "search-runtime" }
  container_definitions = jsonencode([
    each.key == "catalog" ? local.asset_init : local.ca_init,
    {
      name                   = each.key
      image                  = each.value.image
      essential              = true
      user                   = "10001:10001"
      memory                 = 1536
      readonlyRootFilesystem = true
      linuxParameters        = local.linux_parameters
      entryPoint             = ["python", "-c", file("${path.module}/launch.py")]
      command                = [each.key]
      dependsOn              = [{ containerName = each.key == "catalog" ? "assets-init" : "ca-init", condition = "SUCCESS" }]
      environment            = [for name, value in merge(local.common_environment, each.value.environment) : { name = name, value = value }]
      secrets = [for name in each.value.secret_keys : {
        name = name, valueFrom = "${aws_secretsmanager_secret.runtime[each.key].arn}:${name}::"
      }]
      mountPoints = [
        { sourceVolume = "shared", containerPath = "/shared", readOnly = true },
        { sourceVolume = "search-runtime", containerPath = "/runtime", readOnly = false },
      ]
      stopTimeout      = 120
      startTimeout     = 120
      logConfiguration = local.logging
    },
  ])
  depends_on = [terraform_data.guard, aws_iam_role_policy.secret_injection, aws_iam_role_policy.assets]
}

resource "aws_ecs_service" "ai" {
  name            = local.name
  cluster         = var.config.cluster_arn
  task_definition = aws_ecs_task_definition.app.arn
  desired_count   = var.config.desired_count
  capacity_provider_strategy {
    capacity_provider = var.config.capacity_provider
    weight            = 1
  }
  network_configuration {
    subnets          = [var.config.subnet_id]
    security_groups  = [aws_security_group.task.id, var.config.database_client_security_group_id]
    assign_public_ip = false
  }
  # 중단 허용 AI 단일 Task: 두 세대 동시 실행·유료 호출 중복을 피하는 Stop/Start 배포.
  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }
  service_connect_configuration {
    enabled   = true
    namespace = aws_service_discovery_http_namespace.services.arn
    dynamic "service" {
      for_each = { chat = 7860, profiler = 8001 }
      content {
        port_name      = "ai-${service.key}"
        discovery_name = "ai-${service.key}"
        timeout {
          # Chat SSE는 총 요청 시간 제한을 끄고 무응답 연결만 제한한다.
          idle_timeout_seconds        = service.key == "chat" ? 300 : 120
          per_request_timeout_seconds = service.key == "chat" ? 0 : 120
        }
        client_alias {
          dns_name = "ai-${service.key}"
          port     = service.value
        }
      }
    }
    log_configuration {
      log_driver = "awslogs"
      options    = merge(local.logging.options, { awslogs-stream-prefix = "service-connect" })
    }
  }
  lifecycle {
    # 최초 등록은 Terraform, 이후 이미지 Revision 선택은 Cloud CD가 관리한다.
    ignore_changes = [task_definition]
  }
  depends_on = [terraform_data.guard, aws_vpc_security_group_ingress_rule.from_backend, aws_vpc_security_group_egress_rule.backend_to_ai]
}
