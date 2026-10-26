mock_provider "aws" {
  mock_data "aws_subnet" {
    defaults = { vpc_id = "vpc-staging", availability_zone = "ap-northeast-2a", map_public_ip_on_launch = false }
  }
  mock_data "aws_security_group" {
    defaults = { vpc_id = "vpc-staging" }
  }
  mock_resource "aws_iam_role" {
    defaults = { arn = "arn:aws:iam::000000000000:role/mock" }
  }
  mock_resource "aws_secretsmanager_secret" {
    defaults = { arn = "arn:aws:secretsmanager:ap-northeast-2:000000000000:secret:seonjalal/staging/ai/mock-abcdef" }
  }
  mock_data "aws_vpc_endpoint" {
    defaults = { state = "available", private_dns_enabled = true, security_group_ids = ["sg-secret-endpoint"] }
  }
}

variables {
  aws_account_id             = "000000000000"
  bootstrap_admin_secret_arn = "arn:aws:secretsmanager:ap-northeast-2:000000000000:secret:rds!db-test-AbCdEf"
  bootstrap_expires_at       = timeadd(timestamp(), "2h")
  deployment = {
    vpc_id                            = "vpc-staging"
    subnet_id                         = "subnet-private-a"
    cluster_arn                       = "arn:aws:ecs:ap-northeast-2:000000000000:cluster/seonjalal-v2-staging-cluster"
    capacity_provider                 = "seonjalal-v2-staging-ai"
    database_host                     = "ai-db.example.ap-northeast-2.rds.amazonaws.com"
    database_client_security_group_id = "sg-ai-client"
    backend_security_group_id         = "sg-main-backend"
    backend_export_security_group_id  = "sg-backend-alb"
    backend_base_url                  = "https://v2-staging-origin.seonjalal.com"
    source_sha                        = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    images = {
      chat     = "000000000000.dkr.ecr.ap-northeast-2.amazonaws.com/seonjalal/staging/ai-chat@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
      profiler = "000000000000.dkr.ecr.ap-northeast-2.amazonaws.com/seonjalal/staging/ai-profiler@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
      search   = "000000000000.dkr.ecr.ap-northeast-2.amazonaws.com/seonjalal/staging/ai-search@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
      migrate  = "000000000000.dkr.ecr.ap-northeast-2.amazonaws.com/seonjalal/staging/ai-migrate@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    }
    asset_bucket          = "ai-staging-assets-example"
    asset_prefix          = "ai/staging/verified-release/"
    asset_manifest_key    = "ai/staging/verified-release/manifest.json"
    asset_manifest_sha256 = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    catalog_bundle_s3     = "s3://ai-staging-assets-example/ai/staging/verified-release/catalog/catalog-bundle-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.tar"
    provider_models       = { chat = "synthetic/model", profiler = "synthetic/model" }
    desired_count         = 0
  }
}

run "bootstrap_is_registered_but_not_run" {
  command = plan
  assert {
    condition = (
      length(aws_ecs_task_definition.bootstrap) == 1 &&
      aws_ecs_task_definition.bootstrap[0].family == "seonjalal-v2-staging-ai-db-bootstrap" &&
      module.ai[0].runtime.service_name == "seonjalal-v2-staging-ai" &&
      jsondecode(aws_iam_role_policy.bootstrap_admin_secret[0].policy).Statement[0].Resource == var.bootstrap_admin_secret_arn &&
      length(aws_vpc_security_group_ingress_rule.secrets_from_bootstrap) == 1
    )
    error_message = "관리자 Secret 읽기·AI Secret 세 개 쓰기·Endpoint 접근만 준비하고 Task는 자동 실행하지 않아야 함."
  }
}

run "bootstrap_targets_the_staging_database_only" {
  command = apply
  assert {
    condition = (
      anytrue([for e in jsondecode(aws_ecs_task_definition.bootstrap[0].container_definitions)[0].environment :
      e.name == "AI_DATABASE_NAME" && e.value == "ai_db_staging"]) &&
      strcontains(jsondecode(aws_ecs_task_definition.bootstrap[0].container_definitions)[0].entryPoint[2], "DATABASE = \"ai_db_staging\"") &&
      !strcontains(aws_ecs_task_definition.bootstrap[0].container_definitions, "production") &&
      jsondecode(aws_ecs_task_definition.bootstrap[0].container_definitions)[0].image == var.deployment.images.migrate &&
      jsondecode(aws_ecs_task_definition.bootstrap[0].container_definitions)[0].logConfiguration.options["awslogs-group"] == "/ecs/seonjalal-v2-staging-ai"
    )
    error_message = "스테이징 DB 이름·스테이징 로그 그룹·migrate 이미지만 사용하고 운영 값은 없어야 함."
  }
}

run "bootstrap_runs_hardened_without_plaintext_secrets" {
  command = apply
  assert {
    condition = (
      jsondecode(aws_ecs_task_definition.bootstrap[0].container_definitions)[0].readonlyRootFilesystem == true &&
      jsondecode(aws_ecs_task_definition.bootstrap[0].container_definitions)[0].user == "10001:10001" &&
      aws_ecs_task_definition.bootstrap[0].network_mode == "awsvpc" &&
      alltrue([for s in jsondecode(aws_ecs_task_definition.bootstrap[0].container_definitions)[0].secrets :
      startswith(s.valueFrom, var.bootstrap_admin_secret_arn)]) &&
      alltrue([for e in jsondecode(aws_ecs_task_definition.bootstrap[0].container_definitions)[0].environment :
      !contains(["ADMIN_PASSWORD", "ADMIN_USERNAME"], e.name)])
    )
    error_message = "읽기 전용 루트·비루트 사용자·awsvpc이고 관리자 값은 환경 변수 평문이 아니라 Secret 참조여야 함."
  }
}

run "write_access_is_limited_to_the_three_staging_ai_secrets_and_expires" {
  command = apply
  assert {
    condition = (
      length(jsondecode(aws_iam_role_policy.bootstrap_write[0].policy).Statement) == 1 &&
      toset(jsondecode(aws_iam_role_policy.bootstrap_write[0].policy).Statement[0].Action) == toset(["secretsmanager:DescribeSecret", "secretsmanager:GetSecretValue", "secretsmanager:PutSecretValue"]) &&
      length(jsondecode(aws_iam_role_policy.bootstrap_write[0].policy).Statement[0].Resource) == 3 &&
      can(jsondecode(aws_iam_role_policy.bootstrap_write[0].policy).Statement[0].Condition.DateLessThan["aws:CurrentTime"]) &&
      can(jsondecode(aws_iam_role_policy.bootstrap_admin_secret[0].policy).Statement[0].Condition.DateLessThan["aws:CurrentTime"])
    )
    error_message = "AI Secret 세 개에만 쓰고 두 정책 모두 만료 시각 조건이 있어야 함."
  }
}

run "nothing_is_registered_without_an_admin_secret" {
  command = plan
  variables {
    bootstrap_admin_secret_arn = null
    bootstrap_expires_at       = null
  }
  assert {
    condition = (
      length(aws_ecs_task_definition.bootstrap) == 0 &&
      length(aws_iam_role.bootstrap_execution) == 0 &&
      length(aws_iam_role.bootstrap_task) == 0 &&
      length(aws_vpc_security_group_ingress_rule.secrets_from_bootstrap) == 0
    )
    error_message = "관리자 Secret 입력이 없으면 부트스트랩 리소스를 하나도 만들지 않아야 함."
  }
}

run "missing_expiry_is_rejected" {
  command = plan
  variables {
    bootstrap_expires_at = null
  }
  expect_failures = [aws_iam_role_policy.bootstrap_admin_secret]
}

run "past_expiry_is_rejected" {
  command = plan
  variables {
    bootstrap_expires_at = timeadd(timestamp(), "-1h")
  }
  expect_failures = [aws_iam_role_policy.bootstrap_admin_secret]
}

run "expiry_beyond_four_hours_is_rejected" {
  command = plan
  variables {
    bootstrap_expires_at = timeadd(timestamp(), "6h")
  }
  expect_failures = [aws_iam_role_policy.bootstrap_admin_secret]
}

run "non_rds_secret_is_rejected" {
  command = plan
  variables {
    bootstrap_admin_secret_arn = "arn:aws:secretsmanager:ap-northeast-2:000000000000:secret:seonjalal/staging/ai/runtime-abcdef"
  }
  expect_failures = [var.bootstrap_admin_secret_arn]
}
