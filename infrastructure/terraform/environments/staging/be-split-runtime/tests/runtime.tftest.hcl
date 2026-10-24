mock_provider "aws" {
  mock_data "aws_subnet" {
    defaults = { vpc_id = "vpc-staging", availability_zone = "ap-northeast-2a", map_public_ip_on_launch = false }
  }
  mock_data "aws_security_group" { defaults = { vpc_id = "vpc-staging" } }
  mock_data "aws_lb" { defaults = { vpc_id = "vpc-staging", internal = true } }
  mock_data "aws_lb_listener" {
    defaults = {
      load_balancer_arn = "arn:aws:elasticloadbalancing:ap-northeast-2:000000000000:loadbalancer/app/staging/example"
      port              = 443
      protocol          = "HTTPS"
    }
  }
}

override_data {
  target = data.aws_subnet.app["subnet-private-b"]
  values = { vpc_id = "vpc-staging", availability_zone = "ap-northeast-2b", map_public_ip_on_launch = false }
}
override_resource {
  target          = aws_secretsmanager_secret.runtime[0]
  override_during = plan
  values          = { arn = "arn:aws:secretsmanager:ap-northeast-2:000000000000:secret:seonjalal/staging/backend/split-runtime-aaaaaa" }
}

variables {
  environment    = "staging"
  aws_account_id = "000000000000"
  aws_region     = "ap-northeast-2"
  rds_ca_bundle  = "-----BEGIN CERTIFICATE-----\nsynthetic-test-only\n-----END CERTIFICATE-----"
  config = {
    vpc_id                             = "vpc-staging"
    subnet_ids                         = ["subnet-private-a", "subnet-private-b"]
    cluster_arn                        = "arn:aws:ecs:ap-northeast-2:000000000000:cluster/seonjalal-v2-staging-cluster"
    capacity_provider                  = "seonjalal-v2-staging-application"
    namespace_arn                      = "arn:aws:servicediscovery:ap-northeast-2:000000000000:namespace/ns-abc123"
    alb_arn                            = "arn:aws:elasticloadbalancing:ap-northeast-2:000000000000:loadbalancer/app/staging/example"
    alb_listener_arn                   = "arn:aws:elasticloadbalancing:ap-northeast-2:000000000000:listener/app/staging/example/https"
    alb_security_group_id              = "sg-staging-alb"
    database_security_group_id         = "sg-staging-mysql"
    secrets_endpoint_security_group_id = "sg-staging-secrets"
    application_domain_name            = "v2-staging.example.invalid"
    image_repository                   = "seonjalal/production/backend"
    images = {
      main_backend   = "000000000000.dkr.ecr.ap-northeast-2.amazonaws.com/seonjalal/production/backend@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
      payment_server = "000000000000.dkr.ecr.ap-northeast-2.amazonaws.com/seonjalal/production/backend@sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    }
    source_sha           = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    database_host        = "staging-mysql.example.ap-northeast-2.rds.amazonaws.com"
    database_name        = "gift_staging"
    product_image_bucket = "gift-staging-images-example"
    product_image_prefix = "products/staging/"
    main_environment = {
      DB_URL                         = "jdbc:mysql://staging-mysql.example.ap-northeast-2.rds.amazonaws.com:3306/gift_staging?sslMode=VERIFY_IDENTITY"
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
      DB_URL      = "jdbc:mysql://staging-mysql.example.ap-northeast-2.rds.amazonaws.com:3306/gift_staging?sslMode=VERIFY_IDENTITY"
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
        anytrue([for host in condition.host_header : toset(host.values) == toset(["seonjalal-v2-staging-prewarm.invalid"])])
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
      jsondecode(aws_iam_role_policy.product_images[0].policy).Statement[0].Action == ["s3:GetObject"] &&
      jsondecode(aws_iam_role_policy.product_images[0].policy).Statement[0].Resource == "arn:aws:s3:::gift-staging-images-example/products/staging/*"
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
      anytrue([for condition in aws_lb_listener_rule.main_cutover[0].condition :
        anytrue([for host in condition.host_header : toset(host.values) == toset(["v2-staging.example.invalid"])])
      ])
    )
    error_message = "승인된 두 AZ Task와 별도 Host Rule 전환만 허용해야 합니다."
  }
}

run "flyway_runs_only_in_main_with_longer_startup" {
  command = plan
  module { source = "../../../modules/be_split_runtime" }
  variables {
    config = merge(var.config, {
      flyway_enabled   = true
      main_environment = merge(var.config.main_environment, { SPRING_FLYWAY_USER = "gift_migrator" })
      main_secret_keys = concat(tolist(var.config.main_secret_keys), ["SPRING_FLYWAY_PASSWORD"])
    })
  }
  assert {
    condition = (
      anytrue([for e in jsondecode(aws_ecs_task_definition.service["main_backend"].container_definitions)[0].environment : e.name == "SPRING_FLYWAY_ENABLED" && e.value == "true"]) &&
      anytrue([for e in jsondecode(aws_ecs_task_definition.service["payment_server"].container_definitions)[0].environment : e.name == "SPRING_FLYWAY_ENABLED" && e.value == "false"])
    )
    error_message = "Flyway는 Main에서만 켜지고 Payment는 항상 꺼져 있어야 합니다."
  }
  assert {
    condition = (
      jsondecode(aws_ecs_task_definition.service["main_backend"].container_definitions)[0].healthCheck.startPeriod == 300 &&
      jsondecode(aws_ecs_task_definition.service["payment_server"].container_definitions)[0].healthCheck.startPeriod == 90 &&
      aws_ecs_service.service["main_backend"].health_check_grace_period_seconds == 600 &&
      aws_ecs_service.service["payment_server"].health_check_grace_period_seconds == null
    )
    error_message = "Flyway 사용 시 Main의 시작 유예만 늘어나야 합니다(컨테이너 300초, 서비스 600초)."
  }
  assert {
    condition     = anytrue([for s in jsondecode(aws_ecs_task_definition.service["main_backend"].container_definitions)[0].secrets : s.name == "SPRING_FLYWAY_PASSWORD"]) && !anytrue([for e in jsondecode(aws_ecs_task_definition.service["main_backend"].container_definitions)[0].environment : e.name == "SPRING_FLYWAY_PASSWORD"])
    error_message = "Flyway 비밀번호는 Secret 주입으로만 전달되어야 합니다."
  }
}

run "flyway_default_off_keeps_original_startup" {
  command = plan
  module { source = "../../../modules/be_split_runtime" }
  assert {
    condition = (
      jsondecode(aws_ecs_task_definition.service["main_backend"].container_definitions)[0].healthCheck.startPeriod == 90 &&
      aws_ecs_service.service["main_backend"].health_check_grace_period_seconds == 180
    )
    error_message = "기본값(Flyway 꺼짐)에서는 기존 시작 유예(90초/180초)가 유지되어야 합니다."
  }
}

run "flyway_without_dedicated_account_is_rejected" {
  command = plan
  module { source = "../../../modules/be_split_runtime" }
  variables { config = merge(var.config, { flyway_enabled = true }) }
  expect_failures = [terraform_data.guard]
}

run "flyway_with_same_user_as_app_is_rejected" {
  command = plan
  module { source = "../../../modules/be_split_runtime" }
  variables {
    config = merge(var.config, {
      flyway_enabled   = true
      main_environment = merge(var.config.main_environment, { SPRING_FLYWAY_USER = "gift_app" })
      main_secret_keys = concat(tolist(var.config.main_secret_keys), ["SPRING_FLYWAY_PASSWORD"])
    })
  }
  expect_failures = [terraform_data.guard]
}

run "bridge_mode_replaces_task_enis_with_host_level_rules" {
  command = plan
  module { source = "../../../modules/be_split_runtime" }
  variables {
    config = merge(var.config, {
      network_mode                         = "bridge"
      container_instance_security_group_id = "sg-staging-instance"
    })
  }
  assert {
    condition = (
      length(aws_security_group.task) == 0 &&
      length(aws_vpc_security_group_ingress_rule.main_from_alb) == 0 &&
      length(aws_vpc_security_group_egress_rule.alb_to_main) == 0 &&
      length(aws_vpc_security_group_ingress_rule.payment_from_main) == 0 &&
      length(aws_vpc_security_group_egress_rule.main_to_payment) == 0 &&
      length(aws_vpc_security_group_ingress_rule.database) == 0 &&
      length(aws_vpc_security_group_egress_rule.database) == 0 &&
      length(aws_vpc_security_group_egress_rule.https) == 0 &&
      length(aws_vpc_security_group_ingress_rule.secrets_endpoint) == 0
    )
    error_message = "bridge에서는 Task 보안 그룹과 Task 기준 규칙을 만들지 않아야 합니다."
  }
  assert {
    condition = (
      alltrue([for task in values(aws_ecs_task_definition.service) : task.network_mode == "bridge"]) &&
      alltrue([for task in values(aws_ecs_task_definition.service) : jsondecode(task.container_definitions)[0].portMappings[0].hostPort == 0]) &&
      alltrue([for service in values(aws_ecs_service.service) : length(service.network_configuration) == 0]) &&
      aws_lb_target_group.main.target_type == "instance"
    )
    error_message = "bridge는 동적 호스트 포트(0), network_configuration 없음, instance 대상 그룹이어야 합니다."
  }
  assert {
    condition = (
      aws_vpc_security_group_ingress_rule.instance_from_alb[0].security_group_id == "sg-staging-instance" &&
      aws_vpc_security_group_ingress_rule.instance_from_alb[0].referenced_security_group_id == "sg-staging-alb" &&
      aws_vpc_security_group_ingress_rule.instance_from_alb[0].from_port == 32768 &&
      aws_vpc_security_group_ingress_rule.instance_from_alb[0].to_port == 65535 &&
      aws_vpc_security_group_egress_rule.alb_to_instances[0].security_group_id == "sg-staging-alb" &&
      aws_vpc_security_group_egress_rule.alb_to_instances[0].referenced_security_group_id == "sg-staging-instance" &&
      aws_vpc_security_group_egress_rule.alb_to_instances[0].from_port == 32768 &&
      aws_vpc_security_group_egress_rule.alb_to_instances[0].to_port == 65535
    )
    error_message = "ALB와 호스트 보안 그룹 사이에 동적 포트 범위(32768-65535) 규칙이 있어야 합니다."
  }
  assert {
    condition = (
      aws_vpc_security_group_ingress_rule.instance_self[0].security_group_id == "sg-staging-instance" &&
      aws_vpc_security_group_ingress_rule.instance_self[0].referenced_security_group_id == "sg-staging-instance" &&
      aws_vpc_security_group_ingress_rule.database_from_instances[0].security_group_id == "sg-staging-mysql" &&
      aws_vpc_security_group_ingress_rule.database_from_instances[0].referenced_security_group_id == "sg-staging-instance" &&
      aws_vpc_security_group_ingress_rule.database_from_instances[0].from_port == 3306
    )
    error_message = "Service Connect용 호스트 자기 참조 규칙과 DB의 호스트 보안 그룹 허용 규칙이 있어야 합니다."
  }
}

run "default_network_mode_stays_awsvpc" {
  command = plan
  module { source = "../../../modules/be_split_runtime" }
  assert {
    condition = (
      length(aws_security_group.task) == 2 &&
      alltrue([for task in values(aws_ecs_task_definition.service) : task.network_mode == "awsvpc"]) &&
      alltrue([for task in values(aws_ecs_task_definition.service) : try(jsondecode(task.container_definitions)[0].portMappings[0].hostPort, null) == null]) &&
      alltrue([for service in values(aws_ecs_service.service) : length(service.network_configuration) == 1]) &&
      aws_lb_target_group.main.target_type == "ip" &&
      length(aws_vpc_security_group_ingress_rule.instance_from_alb) == 0 &&
      length(aws_vpc_security_group_egress_rule.alb_to_instances) == 0 &&
      length(aws_vpc_security_group_ingress_rule.instance_self) == 0 &&
      length(aws_vpc_security_group_ingress_rule.database_from_instances) == 0
    )
    error_message = "기본값은 기존 awsvpc 구성(Task 보안 그룹, ip 대상 그룹, network_configuration)이 그대로여야 합니다."
  }
}

run "bridge_requires_instance_security_group" {
  command = plan
  module { source = "../../../modules/be_split_runtime" }
  variables { config = merge(var.config, { network_mode = "bridge" }) }
  expect_failures = [terraform_data.guard]
}

run "unknown_network_mode_is_rejected" {
  command = plan
  module { source = "../../../modules/be_split_runtime" }
  variables { config = merge(var.config, { network_mode = "host" }) }
  expect_failures = [terraform_data.guard]
}

run "bridge_instance_security_group_from_other_vpc_is_rejected" {
  command = plan
  module { source = "../../../modules/be_split_runtime" }
  variables {
    config = merge(var.config, {
      network_mode                         = "bridge"
      container_instance_security_group_id = "sg-other-vpc"
    })
  }
  override_data {
    target = data.aws_security_group.instance[0]
    values = { vpc_id = "vpc-other" }
  }
  expect_failures = [terraform_data.guard]
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
  variables { config = merge(var.config, { payment_environment = merge(var.config.payment_environment, { DB_URL = "jdbc:mysql://staging-mysql.example.ap-northeast-2.rds.amazonaws.com:3306/gift_staging?sslMode=DISABLED" }) }) }
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
      cluster_arn = "arn:aws:ecs:ap-northeast-2:000000000000:cluster/seonjalal-v2-production-cluster"
    })
  }
  expect_failures = [terraform_data.guard]
}

run "public_subnet_is_rejected" {
  command = plan
  module { source = "../../../modules/be_split_runtime" }
  override_data {
    target = data.aws_subnet.app["subnet-private-b"]
    values = { vpc_id = "vpc-staging", availability_zone = "ap-northeast-2b", map_public_ip_on_launch = true }
  }
  expect_failures = [terraform_data.guard]
}

run "staging_cutover_without_health_is_rejected" {
  command = plan
  module { source = "../../../modules/be_split_runtime" }
  variables { config = merge(var.config, { traffic_cutover = true }) }
  expect_failures = [var.config]
}

# ---- 블루-그린 슬롯 ----
# 접미사 슬롯: 기존 슬롯의 IAM Role·Secret·로그 그룹을 재사용하고 이름·Service Connect·예약 Rule만 분리한다.
run "green_slot_reuses_shared_resources_and_isolates_names" {
  command = plan
  module { source = "../../../modules/be_split_runtime" }
  variables {
    config = merge(var.config, {
      network_mode                         = "bridge"
      container_instance_security_group_id = "sg-staging-instance"
      name_suffix                          = "-b"
      parking_rule_priority                = 7
      shared_resources = {
        execution_role_arn = "arn:aws:iam::000000000000:role/seonjalal-v2-staging-split-execution"
        task_role_arns = {
          main_backend   = "arn:aws:iam::000000000000:role/seonjalal-v2-staging-main-backend-task"
          payment_server = "arn:aws:iam::000000000000:role/seonjalal-v2-staging-payment-server-task"
        }
        secret_arn = "arn:aws:secretsmanager:ap-northeast-2:000000000000:secret:seonjalal/staging/backend/split-runtime-aaaaaa"
      }
    })
  }
  assert {
    condition = (
      length(aws_cloudwatch_log_group.service) == 0 &&
      length(aws_iam_role.task) == 0 &&
      length(aws_iam_role.execution) == 0 &&
      length(aws_iam_role_policy.secrets) == 0 &&
      length(aws_iam_role_policy.product_images) == 0 &&
      length(aws_secretsmanager_secret.runtime) == 0
    )
    error_message = "접미사 슬롯은 공유 자원(로그 그룹·IAM·Secret)을 새로 만들지 않아야 합니다."
  }
  assert {
    condition = (
      aws_ecs_service.service["main_backend"].name == "seonjalal-v2-staging-main-backend-b" &&
      aws_ecs_service.service["payment_server"].name == "seonjalal-v2-staging-payment-server-b" &&
      aws_ecs_task_definition.service["main_backend"].family == "seonjalal-v2-staging-main-backend-b" &&
      aws_ecs_task_definition.service["payment_server"].family == "seonjalal-v2-staging-payment-server-b"
    )
    error_message = "서비스·Task 패밀리 이름에 접미사가 붙어야 합니다."
  }
  assert {
    condition = (
      aws_ecs_task_definition.service["main_backend"].execution_role_arn == "arn:aws:iam::000000000000:role/seonjalal-v2-staging-split-execution" &&
      aws_ecs_task_definition.service["main_backend"].task_role_arn == "arn:aws:iam::000000000000:role/seonjalal-v2-staging-main-backend-task" &&
      aws_ecs_task_definition.service["payment_server"].task_role_arn == "arn:aws:iam::000000000000:role/seonjalal-v2-staging-payment-server-task" &&
      startswith(jsondecode(aws_ecs_task_definition.service["main_backend"].container_definitions)[0].secrets[0].valueFrom, "arn:aws:secretsmanager:ap-northeast-2:000000000000:secret:seonjalal/staging/backend/split-runtime-aaaaaa:") &&
      jsondecode(aws_ecs_task_definition.service["main_backend"].container_definitions)[0].logConfiguration.options["awslogs-group"] == "/ecs/seonjalal-v2-staging-main-backend"
    )
    error_message = "공유 IAM Role·Secret·기존 로그 그룹을 그대로 사용해야 합니다."
  }
  assert {
    condition = (
      anytrue([for env in jsondecode(aws_ecs_task_definition.service["main_backend"].container_definitions)[0].environment :
      env.name == "PAYMENT_SERVER_BASE_URL" && env.value == "http://payment-server-b:8090"]) &&
      aws_ecs_service.service["payment_server"].service_connect_configuration[0].service[0].discovery_name == "payment-server-b" &&
      aws_ecs_service.service["payment_server"].service_connect_configuration[0].service[0].client_alias[0].dns_name == "payment-server-b"
    )
    error_message = "새 슬롯의 Main은 같은 슬롯의 Payment만 호출하도록 Service Connect 이름이 분리되어야 합니다."
  }
  assert {
    condition = (
      aws_lb_listener_rule.main_parking[0].priority == 7 &&
      anytrue([for condition in aws_lb_listener_rule.main_parking[0].condition :
      anytrue([for host in condition.host_header : toset(host.values) == toset(["seonjalal-v2-staging-prewarm-b.invalid"])])]) &&
      length(aws_lb_listener_rule.main_cutover) == 0
    )
    error_message = "예약 Rule은 슬롯별 우선순위·Host를 쓰고 전환 Rule은 만들지 않아야 합니다."
  }
}

# 두 슬롯이 모두 bridge일 때 호스트 보안 그룹 규칙은 한 슬롯만 소유한다(중복 규칙 오류 방지).
run "green_bridge_slot_can_leave_host_rules_to_the_existing_slot" {
  command = plan
  module { source = "../../../modules/be_split_runtime" }
  variables {
    config = merge(var.config, {
      network_mode                         = "bridge"
      container_instance_security_group_id = "sg-staging-instance"
      manage_host_rules                    = false
      name_suffix                          = "-b"
      parking_rule_priority                = 7
      shared_resources = {
        execution_role_arn = "arn:aws:iam::000000000000:role/x"
        task_role_arns     = { main_backend = "arn:aws:iam::000000000000:role/a", payment_server = "arn:aws:iam::000000000000:role/b" }
        secret_arn         = "arn:aws:secretsmanager:ap-northeast-2:000000000000:secret:x"
      }
    })
  }
  assert {
    condition = (
      length(aws_vpc_security_group_ingress_rule.instance_from_alb) == 0 &&
      length(aws_vpc_security_group_egress_rule.alb_to_instances) == 0 &&
      length(aws_vpc_security_group_ingress_rule.instance_self) == 0 &&
      length(aws_vpc_security_group_ingress_rule.database_from_instances) == 0 &&
      length(aws_security_group.task) == 0 &&
      aws_lb_target_group.main.target_type == "instance"
    )
    error_message = "규칙을 소유하지 않는 bridge 슬롯은 호스트 보안 그룹 규칙을 만들지 않아야 합니다."
  }
}

run "green_slot_without_shared_resources_is_rejected" {
  command = plan
  module { source = "../../../modules/be_split_runtime" }
  variables {
    config = merge(var.config, { name_suffix = "-b" })
  }
  expect_failures = [terraform_data.guard]
}

run "shared_resources_without_suffix_is_rejected" {
  command = plan
  module { source = "../../../modules/be_split_runtime" }
  variables {
    config = merge(var.config, {
      shared_resources = {
        execution_role_arn = "arn:aws:iam::000000000000:role/x"
        task_role_arns     = { main_backend = "arn:aws:iam::000000000000:role/a", payment_server = "arn:aws:iam::000000000000:role/b" }
        secret_arn         = "arn:aws:secretsmanager:ap-northeast-2:000000000000:secret:x"
      }
    })
  }
  expect_failures = [terraform_data.guard]
}

run "green_slot_cannot_own_the_cutover_rule" {
  command = plan
  module { source = "../../../modules/be_split_runtime" }
  variables {
    config = merge(var.config, {
      name_suffix             = "-b"
      desired_count           = 2
      traffic_cutover         = true
      traffic_health_verified = true
      activation_checks = {
        database_migrated = true, network_verified = true, capacity_verified = true,
        secrets_ready     = true, images_verified = true, api_compatible = true
      }
      shared_resources = {
        execution_role_arn = "arn:aws:iam::000000000000:role/x"
        task_role_arns     = { main_backend = "arn:aws:iam::000000000000:role/a", payment_server = "arn:aws:iam::000000000000:role/b" }
        secret_arn         = "arn:aws:secretsmanager:ap-northeast-2:000000000000:secret:x"
      }
    })
  }
  expect_failures = [terraform_data.guard]
}

# 기존 슬롯의 전환 Rule: 새 슬롯이 없으면 단일 Target, 있으면 가중치 전달.
run "cutover_without_green_keeps_single_target_form" {
  command = plan
  module { source = "../../../modules/be_split_runtime" }
  variables {
    config = merge(var.config, {
      desired_count           = 2
      traffic_cutover         = true
      traffic_health_verified = true
      activation_checks = {
        database_migrated = true, network_verified = true, capacity_verified = true,
        secrets_ready     = true, images_verified = true, api_compatible = true
      }
    })
  }
  override_resource {
    target          = aws_lb_target_group.main
    override_during = plan
    values          = { arn = "arn:aws:elasticloadbalancing:ap-northeast-2:000000000000:targetgroup/blue/0123456789abcdef" }
  }
  assert {
    condition = (
      aws_lb_listener_rule.main_cutover[0].action[0].target_group_arn == aws_lb_target_group.main.arn &&
      length(aws_lb_listener_rule.main_cutover[0].action[0].forward) == 0
    )
    error_message = "새 슬롯이 없으면 기존 Rule 형태(단일 Target Group)를 유지해야 합니다."
  }
}

run "cutover_with_green_splits_traffic_by_weight" {
  command = plan
  module { source = "../../../modules/be_split_runtime" }
  variables {
    config = merge(var.config, {
      desired_count           = 2
      traffic_cutover         = true
      traffic_health_verified = true
      activation_checks = {
        database_migrated = true, network_verified = true, capacity_verified = true,
        secrets_ready     = true, images_verified = true, api_compatible = true
      }
      green_target_group = {
        arn    = "arn:aws:elasticloadbalancing:ap-northeast-2:000000000000:targetgroup/green/0123456789abcdef"
        weight = 10
      }
    })
  }
  override_resource {
    target          = aws_lb_target_group.main
    override_during = plan
    values          = { arn = "arn:aws:elasticloadbalancing:ap-northeast-2:000000000000:targetgroup/blue/0123456789abcdef" }
  }
  assert {
    condition = (
      length(aws_lb_listener_rule.main_cutover[0].action[0].forward) == 1 &&
      length(aws_lb_listener_rule.main_cutover[0].action[0].forward[0].target_group) == 2 &&
      one([for tg in aws_lb_listener_rule.main_cutover[0].action[0].forward[0].target_group : tg.weight if tg.arn == aws_lb_target_group.main.arn]) == 90 &&
      one([for tg in aws_lb_listener_rule.main_cutover[0].action[0].forward[0].target_group : tg.weight if tg.arn == "arn:aws:elasticloadbalancing:ap-northeast-2:000000000000:targetgroup/green/0123456789abcdef"]) == 10
    )
    error_message = "기존 90, 새 슬롯 10 가중치로 나누어야 합니다."
  }
}

run "green_weight_out_of_range_is_rejected" {
  command = plan
  module { source = "../../../modules/be_split_runtime" }
  variables {
    config = merge(var.config, {
      desired_count           = 2
      traffic_cutover         = true
      traffic_health_verified = true
      activation_checks = {
        database_migrated = true, network_verified = true, capacity_verified = true,
        secrets_ready     = true, images_verified = true, api_compatible = true
      }
      green_target_group = {
        arn    = "arn:aws:elasticloadbalancing:ap-northeast-2:000000000000:targetgroup/green/0123456789abcdef"
        weight = 101
      }
    })
  }
  expect_failures = [terraform_data.guard]
}

run "green_weight_requires_cutover" {
  command = plan
  module { source = "../../../modules/be_split_runtime" }
  variables {
    config = merge(var.config, {
      green_target_group = {
        arn    = "arn:aws:elasticloadbalancing:ap-northeast-2:000000000000:targetgroup/green/0123456789abcdef"
        weight = 10
      }
    })
  }
  expect_failures = [terraform_data.guard]
}
