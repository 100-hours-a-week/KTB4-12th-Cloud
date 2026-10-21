mock_provider "aws" {
  mock_data "aws_subnet" {
    defaults = { vpc_id = "vpc-production", availability_zone = "ap-northeast-2a", map_public_ip_on_launch = false }
  }
  mock_data "aws_security_group" {
    defaults = { vpc_id = "vpc-production" }
  }
}

override_resource {
  target          = aws_secretsmanager_secret.runtime["app"]
  override_during = plan
  values          = { arn = "arn:aws:secretsmanager:ap-northeast-2:000000000000:secret:seonjalal/production/ai/runtime-aaaaaa" }
}
override_resource {
  target          = aws_secretsmanager_secret.runtime["migrate"]
  override_during = plan
  values          = { arn = "arn:aws:secretsmanager:ap-northeast-2:000000000000:secret:seonjalal/production/ai/migration-bbbbbb" }
}
override_resource {
  target          = aws_secretsmanager_secret.runtime["catalog"]
  override_during = plan
  values          = { arn = "arn:aws:secretsmanager:ap-northeast-2:000000000000:secret:seonjalal/production/ai/catalog-cccccc" }
}

variables {
  environment    = "production"
  aws_account_id = "000000000000"
  aws_region     = "ap-northeast-2"
  rds_ca_bundle  = "-----BEGIN CERTIFICATE-----\nsynthetic-test-only\n-----END CERTIFICATE-----"
  config = {
    vpc_id                            = "vpc-production"
    subnet_id                         = "subnet-private-a"
    cluster_arn                       = "arn:aws:ecs:ap-northeast-2:000000000000:cluster/seonjalal-v2-production-cluster"
    capacity_provider                 = "seonjalal-v2-production-ai"
    namespace_arn                     = "arn:aws:servicediscovery:ap-northeast-2:000000000000:namespace/ns-a1b2c3d4"
    database_host                     = "production-ai.example.ap-northeast-2.rds.amazonaws.com"
    database_client_security_group_id = "sg-ai-client"
    backend_security_group_id         = "sg-main-backend"
    backend_export_security_group_id  = "sg-backend-https"
    backend_base_url                  = "http://main-backend:8080"
    backend_export_url                = "https://backend.example.invalid/test/export"
    backend_categories_url            = "https://backend.example.invalid/test/categories"
    source_sha                        = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    images = {
      chat     = "000000000000.dkr.ecr.ap-northeast-2.amazonaws.com/seonjalal/production/ai-chat@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
      profiler = "000000000000.dkr.ecr.ap-northeast-2.amazonaws.com/seonjalal/production/ai-profiler@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
      search   = "000000000000.dkr.ecr.ap-northeast-2.amazonaws.com/seonjalal/production/ai-search@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
      migrate  = "000000000000.dkr.ecr.ap-northeast-2.amazonaws.com/seonjalal/production/ai-migrate@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    }
    asset_bucket          = "ai-production-assets-example"
    asset_prefix          = "ai/production/verified-release/"
    asset_manifest_key    = "ai/production/verified-release/manifest.json"
    asset_manifest_sha256 = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    catalog_bundle_s3     = "s3://ai-production-assets-example/ai/production/verified-release/catalog/catalog-bundle-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.tar"
    provider_models       = { chat = "synthetic/model", profiler = "synthetic/model" }
    desired_count         = 0
  }
}

run "prepared_runtime_preserves_localhost_and_job_boundaries" {
  command = plan
  module { source = "../../../modules/ai_ecs_runtime" }
  assert {
    condition = (
      tonumber(aws_ecs_task_definition.app.memory) == 3200 &&
      {
        for c in jsondecode(aws_ecs_task_definition.app.container_definitions) : c.name => c.memory
        } == {
        "assets-init" = 192
        search        = 1280
        profiler      = 640
        chat          = 896
      } &&
      tonumber(aws_ecs_task_definition.app.memory) >= sum([
        for c in jsondecode(aws_ecs_task_definition.app.container_definitions) : c.memory
      ]) + 192 &&
      tonumber(aws_ecs_task_definition.app.memory) <= 3327 - 127
    )
    error_message = "실제 ECS 잔여3327MiB 기준 Task3200MiB·배치 여유127MiB·Proxy 여유192MiB 필요."
  }
  assert {
    condition = (
      aws_ecs_task_definition.app.network_mode == "awsvpc" &&
      toset(aws_ecs_task_definition.app.requires_compatibilities) == toset(["EC2"]) &&
      aws_ecs_task_definition.app.runtime_platform[0].cpu_architecture == "X86_64" &&
      toset([for c in jsondecode(aws_ecs_task_definition.app.container_definitions) : c.name]) == toset(["assets-init", "chat", "profiler", "search"]) &&
      toset(keys(aws_ecs_task_definition.job)) == toset(["migrate", "catalog"]) &&
      anytrue([for c in jsondecode(aws_ecs_task_definition.job["catalog"].container_definitions) :
        c.name == "catalog" && anytrue([for e in c.environment :
          e.name == "CATALOG_BUNDLE_S3" && e.value == var.config.catalog_bundle_s3
        ])
      ]) &&
      alltrue([for c in jsondecode(aws_ecs_task_definition.app.container_definitions) :
        c.readonlyRootFilesystem && c.startTimeout <= 120 && (c.name == "assets-init" || c.user == "10001:10001")
      ])
    )
    error_message = "Chat/Profiler/Search 같은 Task·EC2 awsvpc, 별도 Migration/Catalog Task 및 읽기전용 앱 유지 필요."
  }
  assert {
    condition = alltrue([for c in jsondecode(aws_ecs_task_definition.app.container_definitions) :
      c.name == "assets-init" || (
        contains([for d in c.dependsOn : "${d.containerName}:${d.condition}"], "assets-init:SUCCESS") &&
        (c.name == "search" || contains([for d in c.dependsOn : "${d.containerName}:${d.condition}"], "search:HEALTHY")) &&
        anytrue([for e in c.environment : e.name == "PGSSLMODE" && e.value == "verify-full"]) &&
        alltrue([for e in c.environment : !contains(["DATABASE_URL", "PROFILING_DATABASE_URL", "BE_SERVICE_TOKEN", "AI_INTERNAL_SERVICE_TOKEN"], e.name)])
      )
    ])
    error_message = "자산 검증·Search 준비 순서·TLS검증 및 Secret 직접 값 비포함 계약 필요."
  }
  assert {
    condition = (
      aws_ecs_service.ai.desired_count == 0 &&
      toset([for strategy in aws_ecs_service.ai.capacity_provider_strategy : strategy.capacity_provider]) == toset(["seonjalal-v2-production-ai"]) &&
      !aws_ecs_service.ai.network_configuration[0].assign_public_ip &&
      toset(aws_ecs_service.ai.network_configuration[0].subnets) == toset(["subnet-private-a"]) &&
      aws_ecs_service.ai.deployment_minimum_healthy_percent == 0 &&
      aws_ecs_service.ai.deployment_maximum_percent == 100 &&
      aws_ecs_service.ai.deployment_circuit_breaker[0].rollback &&
      aws_ecs_service.ai.service_connect_configuration[0].namespace == var.config.namespace_arn &&
      length(aws_service_discovery_http_namespace.services) == 0 &&
      anytrue([for svc in aws_ecs_service.ai.service_connect_configuration[0].service :
        svc.discovery_name == "ai-chat" && svc.timeout[0].per_request_timeout_seconds == 0
      ]) &&
      toset(keys(aws_vpc_security_group_ingress_rule.from_backend)) == toset(["chat", "profiler"]) &&
      aws_vpc_security_group_ingress_rule.backend_export.from_port == 8443 &&
      aws_vpc_security_group_ingress_rule.backend_export.to_port == 8443 &&
      one(aws_vpc_security_group_egress_rule.backend_export).from_port == 8443 &&
      one(aws_vpc_security_group_egress_rule.backend_export).to_port == 8443
    )
    error_message = "운영 준비 상태 및 AI 단일세대 배포·Backend 전용 Chat/Profiler 접근 유지 필요."
  }
  assert {
    condition = (
      jsondecode(aws_iam_role_policy.assets["app"].policy).Statement[0].Action == ["s3:GetObject"] &&
      jsondecode(aws_iam_role_policy.assets["app"].policy).Statement[0].Resource == "arn:aws:s3:::ai-production-assets-example/ai/production/verified-release/*" &&
      alltrue([for name, policy in aws_iam_role_policy.secret_injection :
        jsondecode(policy.policy).Statement[0].Action == ["secretsmanager:GetSecretValue"] &&
        jsondecode(policy.policy).Statement[0].Resource == aws_secretsmanager_secret.runtime[name].arn
      ])
    )
    error_message = "자산 읽기 Prefix와 Execution 역할별 Secret을 제한하고 DB 관리자 권한을 포함하지 않아야 함."
  }
}

run "production_without_shared_namespace_is_rejected" {
  command = plan
  module { source = "../../../modules/ai_ecs_runtime" }
  variables { config = merge(var.config, { namespace_arn = null }) }
  expect_failures = [var.config]
}

run "production_without_catalog_bundle_is_rejected" {
  command = plan
  module { source = "../../../modules/ai_ecs_runtime" }
  variables { config = merge(var.config, { catalog_bundle_s3 = null }) }
  expect_failures = [terraform_data.guard]
}

run "activation_without_checks_is_rejected" {
  command = plan
  module { source = "../../../modules/ai_ecs_runtime" }
  variables { config = merge(var.config, { desired_count = 1 }) }
  expect_failures = [terraform_data.guard]
}

run "mutable_image_is_rejected" {
  command = plan
  module { source = "../../../modules/ai_ecs_runtime" }
  variables {
    config = merge(var.config, { images = merge(var.config.images, {
      chat = "000000000000.dkr.ecr.ap-northeast-2.amazonaws.com/seonjalal/production/ai-chat:latest"
    }) })
  }
  expect_failures = [terraform_data.guard]
}

run "cross_environment_cluster_is_rejected" {
  command = plan
  module { source = "../../../modules/ai_ecs_runtime" }
  variables {
    config = merge(var.config, { cluster_arn = "arn:aws:ecs:ap-northeast-2:000000000000:cluster/seonjalal-v2-staging-cluster" })
  }
  expect_failures = [terraform_data.guard]
}

run "backend_capacity_provider_is_rejected" {
  command = plan
  module { source = "../../../modules/ai_ecs_runtime" }
  variables {
    config = merge(var.config, { capacity_provider = "seonjalal-v2-production-application" })
  }
  expect_failures = [terraform_data.guard]
}

run "other_az_is_rejected" {
  command = plan
  module { source = "../../../modules/ai_ecs_runtime" }
  override_data {
    target = data.aws_subnet.app
    values = { vpc_id = "vpc-production", availability_zone = "ap-northeast-2c", map_public_ip_on_launch = false }
  }
  expect_failures = [terraform_data.guard]
}

run "public_subnet_is_rejected" {
  command = plan
  module { source = "../../../modules/ai_ecs_runtime" }
  override_data {
    target = data.aws_subnet.app
    values = { vpc_id = "vpc-production", availability_zone = "ap-northeast-2a", map_public_ip_on_launch = true }
  }
  expect_failures = [terraform_data.guard]
}
