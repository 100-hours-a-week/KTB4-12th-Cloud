mock_provider "aws" {
  mock_data "aws_subnet" {
    defaults = { vpc_id = "vpc-production", availability_zone = "ap-northeast-2a", map_public_ip_on_launch = false }
  }
  mock_data "aws_security_group" { defaults = { vpc_id = "vpc-production" } }
  mock_data "aws_lb" { defaults = { vpc_id = "vpc-production", internal = true } }
  mock_data "aws_lb_listener" {
    defaults = {
      load_balancer_arn = "arn:aws:elasticloadbalancing:ap-northeast-2:000000000000:loadbalancer/app/production/example"
      port              = 8443
      protocol          = "HTTPS"
    }
  }
}

override_data {
  target = data.aws_subnet.app["subnet-private-c"]
  values = { vpc_id = "vpc-production", availability_zone = "ap-northeast-2c", map_public_ip_on_launch = false }
}
override_resource {
  target          = aws_secretsmanager_secret.runtime
  override_during = plan
  values          = { arn = "arn:aws:secretsmanager:ap-northeast-2:000000000000:secret:seonjalal/production/backend/split-runtime-aaaaaa" }
}

variables {
  environment    = "production"
  aws_account_id = "000000000000"
  aws_region     = "ap-northeast-2"
  rds_ca_bundle  = "-----BEGIN CERTIFICATE-----\nsynthetic-test-only\n-----END CERTIFICATE-----"
  config = {
    vpc_id                             = "vpc-production"
    subnet_ids                         = ["subnet-private-a", "subnet-private-c"]
    cluster_arn                        = "arn:aws:ecs:ap-northeast-2:000000000000:cluster/seonjalal-v2-production-cluster"
    capacity_provider                  = "seonjalal-v2-production-application"
    namespace_arn                      = "arn:aws:servicediscovery:ap-northeast-2:000000000000:namespace/ns-abc123"
    alb_arn                            = "arn:aws:elasticloadbalancing:ap-northeast-2:000000000000:loadbalancer/app/production/example"
    alb_listener_arn                   = "arn:aws:elasticloadbalancing:ap-northeast-2:000000000000:listener/app/production/example/https"
    alb_security_group_id              = "sg-production-alb"
    database_security_group_id         = "sg-production-mysql"
    secrets_endpoint_security_group_id = "sg-production-secrets"
    application_domain_name            = "v2-origin.seonjalal.com"
    listener_rule_priority             = 9
    image_repository                   = "seonjalal/production/backend"
    images = {
      main_backend   = "000000000000.dkr.ecr.ap-northeast-2.amazonaws.com/seonjalal/production/backend@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
      payment_server = "000000000000.dkr.ecr.ap-northeast-2.amazonaws.com/seonjalal/production/backend@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    }
    source_sha           = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    database_host        = "production-mysql.example.ap-northeast-2.rds.amazonaws.com"
    database_name        = "seonjalal"
    product_image_bucket = "gift-production-images-example"
    product_image_prefix = "products/production/"
    main_environment = {
      DB_URL                         = "jdbc:mysql://production-mysql.example.ap-northeast-2.rds.amazonaws.com:3306/seonjalal?sslMode=VERIFY_IDENTITY"
      DB_USERNAME                    = "gift_app"
      JWT_ISSUER                     = "example"
      CORS_ALLOWED_ORIGINS           = "https://example.invalid"
      CHAT_CONVERSATION_TOKEN_ISSUER = "example"
      AI_CHAT_CONNECT_TIMEOUT        = "3s"
      AI_CHAT_READ_TIMEOUT           = "120s"
      AI_CHAT_SSE_IDLE_TIMEOUT       = "120s"
      AI_CHAT_SSE_TOTAL_TIMEOUT      = "300s"
      AI_CHAT_SSE_MAX_CONNECTIONS    = "100"
    }
    main_secret_keys = [
      "DB_PASSWORD", "JWT_SECRET_BASE64", "LOGIN_RATE_LIMIT_HMAC_SECRET_BASE64",
      "CHAT_CONVERSATION_TOKEN_SECRET_BASE64", "AI_CHAT_SERVICE_TOKEN",
      "PROFILING_SERVICE_TOKEN", "PAYMENT_SERVER_API_KEY",
    ]
    payment_environment = {
      DB_URL      = "jdbc:mysql://production-mysql.example.ap-northeast-2.rds.amazonaws.com:3306/seonjalal?sslMode=VERIFY_IDENTITY"
      DB_USERNAME = "gift_app"
    }
    payment_secret_keys = ["DB_PASSWORD", "PAYMENT_SERVER_API_KEY"]
    desired_count       = 0
  }
}

run "dormant_split_contract" {
  command = plan
  module { source = "../../../modules/be_split_runtime" }
  assert {
    condition = (
      toset(keys(aws_ecs_task_definition.service)) == toset(["main_backend", "payment_server"]) &&
      toset(keys(aws_ecs_service.service)) == toset(["main_backend", "payment_server"]) &&
      alltrue([for task in values(aws_ecs_task_definition.service) : task.network_mode == "awsvpc" && task.runtime_platform[0].cpu_architecture == "X86_64" && task.skip_destroy]) &&
      alltrue([for service in values(aws_ecs_service.service) : service.desired_count == 0 && !service.network_configuration[0].assign_public_ip && service.service_connect_configuration[0].enabled && service.deployment_circuit_breaker[0].rollback]) &&
      length(aws_lb_listener_rule.main_cutover) == 0 &&
      length(aws_lb_listener_rule.main_parking) == 1 &&
      aws_lb_listener_rule.main_parking[0].priority == 8 &&
      aws_lb_listener_rule.main_parking[0].action[0].type == "forward" &&
      anytrue([for condition in aws_lb_listener_rule.main_parking[0].condition :
        anytrue([for host in condition.host_header : toset(host.values) == toset(["seonjalal-v2-production-prewarm.invalid"])])
      ]) &&
      anytrue([for condition in aws_lb_listener_rule.main_parking[0].condition :
        anytrue([for path in condition.path_pattern : toset(path.values) == toset(["/__seonjalal-be-prewarm"])])
      ])
    )
    error_message = "Main/Payment 분리 Task·휴면 Service·Service Connect 및 전환 미실행 필요."
  }
  assert {
    condition = (
      aws_ecs_service.service["payment_server"].service_connect_configuration[0].service[0].client_alias[0].dns_name == "payment-server" &&
      aws_ecs_service.service["payment_server"].service_connect_configuration[0].service[0].client_alias[0].port == 8090 &&
      aws_ecs_service.service["main_backend"].deployment_minimum_healthy_percent == 100 &&
      aws_ecs_service.service["main_backend"].deployment_maximum_percent == 200 &&
      aws_lb_target_group.main.target_type == "ip" &&
      aws_lb_target_group.main.health_check[0].path == "/actuator/health"
    )
    error_message = "Payment Alias와 Main 배포/Health 계약이 필요."
  }
  assert {
    condition = (
      anytrue([for e in jsondecode(aws_ecs_task_definition.service["main_backend"].container_definitions)[0].environment : e.name == "PAYMENT_SERVER_BASE_URL" && e.value == "http://payment-server:8090"]) &&
      anytrue([for e in jsondecode(aws_ecs_task_definition.service["main_backend"].container_definitions)[0].environment : e.name == "AI_CHAT_BASE_URL" && e.value == "http://ai-chat:7860/v1/chat/conversations"]) &&
      alltrue([for task in values(aws_ecs_task_definition.service) :
        anytrue([for e in jsondecode(task.container_definitions)[0].environment : e.name == "SPRING_FLYWAY_ENABLED" && e.value == "false"])
      ]) &&
      jsondecode(aws_iam_role_policy.product_images.policy).Statement[0].Action == ["s3:GetObject"] &&
      jsondecode(aws_iam_role_policy.product_images.policy).Statement[0].Resource == "arn:aws:s3:::gift-production-images-example/products/production/*"
    )
    error_message = "BE Main 내부 Service Connect 주소 필요."
  }
}

run "verified_two_az_activation_and_host_cutover" {
  command = plan
  module { source = "../../../modules/be_split_runtime" }
  variables {
    config = merge(var.config, {
      desired_count           = 2
      traffic_cutover         = true
      traffic_health_verified = true
      activation_checks = {
        database_migrated = true
        network_verified  = true
        capacity_verified = true
        secrets_ready     = true
        images_verified   = true
        api_compatible    = true
      }
    })
  }
  assert {
    condition = (
      alltrue([for service in values(aws_ecs_service.service) : service.desired_count == 2]) &&
      length(aws_lb_listener_rule.main_cutover) == 1 &&
      aws_lb_listener_rule.main_cutover[0].priority == 9 &&
      anytrue([for condition in aws_lb_listener_rule.main_cutover[0].condition :
        anytrue([for host in condition.host_header : toset(host.values) == toset(["v2-origin.seonjalal.com"])])
      ])
    )
    error_message = "승인된 두 AZ Task와 별도 Host Rule 전환만 허용해야 합니다."
  }
}

run "start_without_checks_is_rejected" {
  command = plan
  module { source = "../../../modules/be_split_runtime" }
  variables { config = merge(var.config, { desired_count = 2 }) }
  expect_failures = [terraform_data.guard]
}

run "mutable_image_is_rejected" {
  command = plan
  module { source = "../../../modules/be_split_runtime" }
  variables { config = merge(var.config, { images = merge(var.config.images, { main_backend = "000000000000.dkr.ecr.ap-northeast-2.amazonaws.com/seonjalal/production/backend:latest" }) }) }
  expect_failures = [terraform_data.guard]
}

run "repository_without_cd_support_is_rejected" {
  command = plan
  module { source = "../../../modules/be_split_runtime" }
  variables {
    config = merge(var.config, {
      image_repository = "seonjalal/staging/backend"
      images = {
        main_backend   = "000000000000.dkr.ecr.ap-northeast-2.amazonaws.com/seonjalal/staging/backend@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        payment_server = "000000000000.dkr.ecr.ap-northeast-2.amazonaws.com/seonjalal/staging/backend@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
      }
    })
  }
  expect_failures = [terraform_data.guard]
}

run "wrong_mysql_tls_is_rejected" {
  command = plan
  module { source = "../../../modules/be_split_runtime" }
  variables { config = merge(var.config, { payment_environment = merge(var.config.payment_environment, { DB_URL = "jdbc:mysql://production-mysql.example.ap-northeast-2.rds.amazonaws.com:3306/seonjalal?sslMode=DISABLED" }) }) }
  expect_failures = [terraform_data.guard]
}

run "plaintext_secret_environment_is_rejected" {
  command = plan
  module { source = "../../../modules/be_split_runtime" }
  variables {
    config = merge(var.config, {
      main_environment = merge(var.config.main_environment, { PAYMENT_SERVER_API_KEY = "example-should-never-enter-state" })
    })
  }
  expect_failures = [terraform_data.guard]
}

run "cross_environment_cluster_is_rejected" {
  command = plan
  module { source = "../../../modules/be_split_runtime" }
  variables {
    config = merge(var.config, {
      cluster_arn = "arn:aws:ecs:ap-northeast-2:000000000000:cluster/seonjalal-v2-staging-cluster"
    })
  }
  expect_failures = [terraform_data.guard]
}

run "public_subnet_is_rejected" {
  command = plan
  module { source = "../../../modules/be_split_runtime" }
  override_data {
    target = data.aws_subnet.app["subnet-private-c"]
    values = { vpc_id = "vpc-production", availability_zone = "ap-northeast-2c", map_public_ip_on_launch = true }
  }
  expect_failures = [terraform_data.guard]
}

run "staging_listener_port_is_rejected" {
  command = plan
  module { source = "../../../modules/be_split_runtime" }
  override_data {
    target = data.aws_lb_listener.https
    values = {
      load_balancer_arn = "arn:aws:elasticloadbalancing:ap-northeast-2:000000000000:loadbalancer/app/production/example"
      port              = 443
      protocol          = "HTTPS"
    }
  }
  expect_failures = [terraform_data.guard]
}

run "wrong_production_origin_host_is_rejected" {
  command = plan
  module { source = "../../../modules/be_split_runtime" }
  variables { config = merge(var.config, { application_domain_name = "www.seonjalal.com" }) }
  expect_failures = [terraform_data.guard]
}

run "production_cutover_without_health_is_rejected" {
  command = plan
  module { source = "../../../modules/be_split_runtime" }
  variables { config = merge(var.config, { traffic_cutover = true }) }
  expect_failures = [var.config]
}
