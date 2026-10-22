# 모든 AWS Provider 호출은 Mock 처리. 실제 계정/백엔드/리소스 접근 없음.
mock_provider "aws" {
  mock_data "aws_instance" {
    defaults = { subnet_id = "subnet-public-a", instance_state = "running", private_ip = "10.20.0.28" }
  }
  mock_data "aws_route53_zone" {
    defaults = { name = "seonjalal.com.", zone_id = "ZEXAMPLE", private_zone = false }
  }
  mock_resource "aws_acm_certificate" {
    defaults = {
      domain_validation_options = [{
        domain_name           = "v2-staging.seonjalal.com"
        resource_record_name  = "_test.v2-staging.seonjalal.com."
        resource_record_type  = "CNAME"
        resource_record_value = "_test.acm-validations.aws."
      }]
    }
  }
}

override_data {
  target = data.aws_subnet.tailscale_router[0]
  values = { vpc_id = "vpc-staging" }
}
override_data {
  target = data.aws_ssm_parameter.ecs_optimized_ami
  values = { value = "ami-0123456789abcdef0" }
}
override_data {
  target = data.aws_subnet.app["subnet-private-a"]
  values = { vpc_id = "vpc-staging", cidr_block = "10.20.10.0/24", availability_zone = "ap-northeast-2a", map_public_ip_on_launch = false, tags = { Environment = "staging" } }
}
override_data {
  target = data.aws_subnet.app["subnet-private-c"]
  values = { vpc_id = "vpc-staging", cidr_block = "10.20.11.0/24", availability_zone = "ap-northeast-2c", map_public_ip_on_launch = false, tags = { Environment = "staging" } }
}
override_data {
  target = data.aws_subnet.public["subnet-public-a"]
  values = { vpc_id = "vpc-staging", availability_zone = "ap-northeast-2a", map_public_ip_on_launch = true }
}
override_data {
  target = data.aws_subnet.public["subnet-public-c"]
  values = { vpc_id = "vpc-staging", availability_zone = "ap-northeast-2c", map_public_ip_on_launch = true }
}
override_resource {
  target = aws_secretsmanager_secret.backend_runtime
  values = { arn = "arn:aws:secretsmanager:ap-northeast-2:000000000000:secret:staging-runtime-test" }
}
override_resource {
  target = aws_ecr_repository.backend
  values = { repository_url = "000000000000.dkr.ecr.ap-northeast-2.amazonaws.com/seonjalal/staging/backend" }
}

variables {
  v1_candidate_secret_role_name      = null
  v1_cutover_network                 = null
  ecs_optimized_ami_id               = null
  aws_account_id                     = "000000000000"
  vpc_id                             = "vpc-staging"
  app_subnet_ids                     = ["subnet-private-a", "subnet-private-c"]
  public_subnet_ids                  = ["subnet-public-a", "subnet-public-c"]
  secrets_endpoint_security_group_id = "sg-0123456789abcdef0"
  database_security_group_id         = "sg-0123456789abcdef1"
  asg_desired_capacity               = 0
  asg_min_size                       = 0
  enable_service                     = false
  database_cutover_verified          = false
  egress_probe_mode                  = false
  enable_runtime_probe               = false
  enable_staging_certificate         = false
  staging_public_zone_id             = "ZEXAMPLE"
  container_secrets                  = {}
  product_image_bucket_arn           = "arn:aws:s3:::staging-example"
  tailscale_router_instance_id       = null
  allowed_client_ipv4_cidrs          = []
  private_egress_verified            = false
  database_fixture_verified          = false
  rds_tls_bootstrap_verified         = true
  enable_alb                         = false
}

run "ai_publisher_limits_identity_and_permissions" {
  command = plan

  override_data {
    target = data.aws_iam_openid_connect_provider.ai_publisher
    values = { arn = "arn:aws:iam::000000000000:oidc-provider/token.actions.githubusercontent.com" }
  }
  override_resource {
    target          = aws_ecr_repository.ai["chat"]
    override_during = plan
    values          = { arn = "arn:aws:ecr:ap-northeast-2:000000000000:repository/seonjalal/staging/ai-chat" }
  }
  override_resource {
    target          = aws_ecr_repository.ai["migrate"]
    override_during = plan
    values          = { arn = "arn:aws:ecr:ap-northeast-2:000000000000:repository/seonjalal/staging/ai-migrate" }
  }
  override_resource {
    target          = aws_ecr_repository.ai["profiler"]
    override_during = plan
    values          = { arn = "arn:aws:ecr:ap-northeast-2:000000000000:repository/seonjalal/staging/ai-profiler" }
  }
  override_resource {
    target          = aws_ecr_repository.ai["search"]
    override_during = plan
    values          = { arn = "arn:aws:ecr:ap-northeast-2:000000000000:repository/seonjalal/staging/ai-search" }
  }

  assert {
    condition = jsondecode(aws_iam_role.ai_image_publisher.assume_role_policy) == {
      Version = "2012-10-17"
      Statement = [{
        Effect    = "Allow"
        Action    = "sts:AssumeRoleWithWebIdentity"
        Principal = { Federated = "arn:aws:iam::000000000000:oidc-provider/token.actions.githubusercontent.com" }
        Condition = { StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          "token.actions.githubusercontent.com:sub" = "repo:100-hours-a-week@167328634/KTB4-12th-AI@1344623933:ref:refs/heads/main"
        } }
      }]
    }
    error_message = "AI main의 불변 owner/repository ID와 STS audience만 허용해야 함. PR/develop/다른 저장소 허용 금지."
  }

  assert {
    condition = (
      length(jsondecode(aws_iam_role_policy.ai_image_publish.policy).Statement) == 2 &&
      jsondecode(aws_iam_role_policy.ai_image_publish.policy).Statement[0].Effect == "Allow" &&
      toset(jsondecode(aws_iam_role_policy.ai_image_publish.policy).Statement[0].Action) == toset(["ecr:GetAuthorizationToken"]) &&
      jsondecode(aws_iam_role_policy.ai_image_publish.policy).Statement[0].Resource == "*" &&
      jsondecode(aws_iam_role_policy.ai_image_publish.policy).Statement[1].Effect == "Allow" &&
      toset(jsondecode(aws_iam_role_policy.ai_image_publish.policy).Statement[1].Resource) == toset([
        for workload in ["chat", "migrate", "profiler", "search"] :
        "arn:aws:ecr:ap-northeast-2:000000000000:repository/seonjalal/staging/ai-${workload}"
      ]) &&
      toset(jsondecode(aws_iam_role_policy.ai_image_publish.policy).Statement[1].Action) == toset([
        "ecr:BatchCheckLayerAvailability", "ecr:BatchGetImage", "ecr:CompleteLayerUpload",
        "ecr:DescribeImages", "ecr:DescribeRepositories", "ecr:GetDownloadUrlForLayer",
        "ecr:InitiateLayerUpload", "ecr:PutImage", "ecr:UploadLayerPart",
      ])
    )
    error_message = "로그인 Token 외에는 스테이징 AI4개 발행·조회만 허용. ECS/IAM/Secret/삭제/다른 환경 권한 추가 금지."
  }
}

run "startup_failure_is_disabled_by_default" {
  command = plan
  assert {
    condition     = !var.inject_startup_failure && strcontains(local.container.command[0], "exec java")
    error_message = "평상시 정상 TLS/JVM 기동 유지 필요."
  }
  assert {
    condition = alltrue([for key, value in {
      Project = "seonjalal", Environment = "staging", TeamSSMAccess = "true"
      } : anytrue([for tag in aws_autoscaling_group.application.tag :
        tag.key == key && tag.value == value && tag.propagate_at_launch
      ])
    ])
    error_message = "스테이징 앱 ASG는 프로젝트/환경/팀 SSM 태그를 신규 EC2에 모두 전파해야 함."
  }
}

run "fixture_failure_exits_without_starting_app" {
  command = plan
  variables {
    inject_startup_failure    = true
    database_fixture_verified = true
  }
  assert {
    condition     = local.container.command[0] == "printf 'STAGING_INJECTED_STARTUP_FAILURE_EXIT_78\\n'; exit 78" && local.container.entryPoint == ["/bin/sh", "-c"]
    error_message = "실패 후보는 DB/JVM 실행 없이 명시된 코드로만 종료 필요."
  }
}

run "startup_failure_rejects_cutover_database" {
  command = plan
  variables {
    inject_startup_failure    = true
    database_fixture_verified = true
    database_cutover_verified = true
  }
  expect_failures = [var.inject_startup_failure]
}

run "pinned_ami_is_not_changed_by_new_recommended_value" {
  command = plan
  variables { ecs_optimized_ami_id = "ami-01111111111111111" }
  assert {
    condition     = aws_launch_template.application.image_id == "ami-01111111111111111"
    error_message = "고정 AMI는 다른 SSM 권장값으로 변경하면 안 됨."
  }
}

run "initial_ami_discovery_can_use_recommended_parameter" {
  command = plan
  assert {
    condition     = aws_launch_template.application.image_id == "ami-0123456789abcdef0"
    error_message = "고정 전 초기 권장 AMI 탐색 동작 유지 필요."
  }
}

run "invalid_ami_pin_is_rejected" {
  command = plan
  variables { ecs_optimized_ami_id = "latest" }
  expect_failures = [var.ecs_optimized_ami_id]
}

run "certificate_does_not_connect_traffic_or_overwrite_records" {
  command = plan
  variables { enable_staging_certificate = true }
  assert {
    condition     = aws_acm_certificate.staging[0].domain_name == "v2-staging.seonjalal.com" && aws_acm_certificate.staging[0].validation_method == "DNS" && aws_acm_certificate.staging[0].key_algorithm == "RSA_2048"
    error_message = "승인된 전용 도메인/DNS 검증만 허용."
  }
  assert {
    condition     = aws_route53_record.staging_certificate_validation[0].type == "CNAME" && !aws_route53_record.staging_certificate_validation[0].allow_overwrite && length(aws_lb.backend) == 0 && length(aws_ecs_service.backend) == 0
    error_message = "검증 CNAME만 생성하며 기존 DNS 덮어쓰기/앱/ALB 연결 금지."
  }
}
run "certificate_rejects_other_zone" {
  command = plan
  variables { enable_staging_certificate = true }
  override_data {
    target = data.aws_route53_zone.staging_public[0]
    values = { name = "other.invalid.", private_zone = false }
  }
  expect_failures = [aws_acm_certificate.staging[0]]
}

run "certificate_accepts_provider_name_without_trailing_dot" {
  command = plan
  variables { enable_staging_certificate = true }
  override_data {
    target = data.aws_route53_zone.staging_public[0]
    values = { name = "seonjalal.com", private_zone = false }
  }
  assert {
    condition     = length(aws_acm_certificate.staging) == 1
    error_message = "동일한 공개 Zone의 마지막 점 생략 표기를 허용해야 함."
  }
}

run "runtime_probe_never_starts_the_application" {
  command = plan
  variables {
    enable_runtime_probe       = true
    egress_probe_mode          = true
    asg_desired_capacity       = 1
    artifact_platform_verified = true
    backend_image              = "000000000000.dkr.ecr.ap-northeast-2.amazonaws.com/seonjalal/staging/backend@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    container_secrets = { for key in ["DB_PASSWORD", "JWT_SECRET_BASE64", "LOGIN_RATE_LIMIT_HMAC_SECRET_BASE64", "PROFILING_SERVICE_TOKEN"] : key => {
      secret_arn = "arn:aws:secretsmanager:ap-northeast-2:000000000000:secret:staging-runtime-test"
      json_key   = key
    } }
  }
  assert {
    condition     = length(aws_ecs_service.backend) == 0 && length(aws_lb.backend) == 0 && jsondecode(aws_ecs_task_definition.runtime_probe[0].container_definitions)[0].entryPoint == ["/bin/sh", "-c"] && length(jsondecode(aws_ecs_task_definition.runtime_probe[0].container_definitions)[0].secrets) == 4
    error_message = "점검은 별도 명령/Secret4개만 사용하고 앱/ALB를 실행하지 않음."
  }
  assert {
    condition     = !strcontains(jsondecode(aws_ecs_task_definition.runtime_probe[0].container_definitions)[0].command[0], "app.jar") && strcontains(jsondecode(aws_ecs_task_definition.runtime_probe[0].container_definitions)[0].command[0], "java -version")
    error_message = "점검에서 백엔드 main/JAR 실행 금지."
  }
}
run "runtime_probe_rejects_incomplete_secrets" {
  command = plan
  variables { enable_runtime_probe = true }
  expect_failures = [aws_ecs_task_definition.runtime_probe[0]]
}

run "completed_probe_definition_can_remain_during_alb_preparation" {
  command = plan
  variables {
    enable_runtime_probe         = true
    private_egress_verified      = true
    asg_desired_capacity         = 1
    enable_alb                   = true
    tailscale_router_instance_id = "i-0123456789abcdef0"
    alb_certificate_arn          = "arn:aws:acm:ap-northeast-2:000000000000:certificate/00000000-0000-0000-0000-000000000000"
    artifact_platform_verified   = true
    backend_image                = "000000000000.dkr.ecr.ap-northeast-2.amazonaws.com/seonjalal/staging/backend@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    container_secrets = { for key in ["DB_PASSWORD", "JWT_SECRET_BASE64", "LOGIN_RATE_LIMIT_HMAC_SECRET_BASE64", "PROFILING_SERVICE_TOKEN"] : key => {
      secret_arn = "arn:aws:secretsmanager:ap-northeast-2:000000000000:secret:staging-runtime-test"
      json_key   = key
    } }
  }
  assert {
    condition     = length(aws_ecs_task_definition.runtime_probe) == 1 && aws_lb.backend[0].internal && length(aws_ecs_service.backend) == 0 && aws_autoscaling_group.application.desired_capacity == 1
    error_message = "ALB 준비 중 완료한 점검 정의 보존 및 앱/호스트 추가 실행 금지."
  }
}

run "shared_product_images_are_read_only" {
  command = plan
  assert {
    condition     = jsondecode(aws_iam_role_policy.product_images[0].policy).Statement[0].Action == ["s3:GetObject"]
    error_message = "공유 개발 이미지 버킷 쓰기/삭제 권한 부여 금지."
  }
}
run "execution_role_reads_only_selected_runtime_secret" {
  command = plan
  variables {
    container_secrets = {
      DB_PASSWORD = {
        secret_arn = "arn:aws:secretsmanager:ap-northeast-2:000000000000:secret:staging-runtime-test"
        json_key   = "DB_PASSWORD"
      }
    }
  }
  assert {
    condition     = jsondecode(aws_iam_role_policy.task_secrets[0].policy).Statement[0].Resource == ["arn:aws:secretsmanager:ap-northeast-2:000000000000:secret:staging-runtime-test"] && jsondecode(aws_iam_role_policy.task_secrets[0].policy).Statement[0].Action == ["secretsmanager:GetSecretValue"]
    error_message = "실행 Role은 명시한 런타임 Secret 읽기만 허용."
  }
}

run "empty_platform_is_safe" {
  command = plan
  assert {
    condition = (
      toset(keys(aws_ecr_repository.ai)) == toset(["chat", "migrate", "profiler", "search"]) &&
      alltrue([for workload, repository in aws_ecr_repository.ai :
        repository.name == "seonjalal/staging/ai-${workload}" &&
        repository.image_tag_mutability == "IMMUTABLE" &&
        !repository.force_delete &&
        repository.image_scanning_configuration[0].scan_on_push &&
        repository.encryption_configuration[0].encryption_type == "AES256"
      ])
    )
    error_message = "AI 스테이징 ECR 4개는 불변 태그·스캔·암호화·강제 삭제 금지 계약 필요."
  }
  assert {
    condition = alltrue([for policy in aws_ecr_lifecycle_policy.ai :
      jsondecode(policy.policy).rules[0].selection.tagStatus == "untagged" &&
      jsondecode(policy.policy).rules[0].selection.countType == "sinceImagePushed" &&
      jsondecode(policy.policy).rules[0].selection.countNumber == 7
    ])
    error_message = "AI ECR은 태그된 롤백 Artifact를 보존하고 미태그 이미지만 7일 후 정리해야 함."
  }
  assert {
    condition     = aws_secretsmanager_secret.backend_runtime.name == "seonjalal/staging/backend/runtime" && aws_secretsmanager_secret.backend_runtime.recovery_window_in_days == 7
    error_message = "런타임 Secret은 전용 이름/삭제 복구 기간 필요. Secret 값은 Terraform에서 관리 금지."
  }
  assert {
    condition     = aws_ecs_cluster.this.name == "seonjalal-v2-staging-cluster" && aws_autoscaling_group.application.desired_capacity == 0
    error_message = "스테이징 이름/기본 용량0 계약 위반."
  }
  assert {
    condition     = length(aws_ecs_service.backend) == 0 && length(aws_lb.backend) == 0 && aws_ecr_repository.backend.image_tag_mutability == "IMMUTABLE"
    error_message = "기본 앱/ALB 비활성화·이미지 불변 계약 위반."
  }
}

run "cannot_boot_without_egress" {
  command = plan
  variables { asg_desired_capacity = 1 }
  expect_failures = [aws_autoscaling_group.application]
}

run "single_private_host_probe_is_allowed" {
  command = plan
  variables {
    asg_desired_capacity = 1
    egress_probe_mode    = true
  }
  assert {
    condition     = aws_autoscaling_group.application.desired_capacity == 1 && length(aws_ecs_service.backend) == 0 && length(aws_lb.backend) == 0 && !var.private_egress_verified
    error_message = "점검 모드는 통신 검증 완료를 가장하거나 앱을 기동하면 안 됨."
  }
}
run "probe_cannot_launch_multiple_hosts" {
  command = plan
  variables {
    asg_desired_capacity = 2
    egress_probe_mode    = true
  }
  expect_failures = [aws_autoscaling_group.application]
}
run "probe_cannot_enable_auto_scaling" {
  command = plan
  variables {
    asg_desired_capacity    = 1
    egress_probe_mode       = true
    managed_scaling_enabled = true
  }
  expect_failures = [aws_autoscaling_group.application]
}

run "cannot_open_to_whole_internet" {
  command = plan
  variables { allowed_client_ipv4_cidrs = ["0.0.0.0/0"] }
  expect_failures = [var.allowed_client_ipv4_cidrs]
}

run "cannot_put_password_in_plain_environment" {
  command = plan
  variables { container_environment = { DB_PASSWORD = "not-a-real-password" } }
  expect_failures = [var.container_environment]
}

run "cannot_mix_vpc_subnets" {
  command = plan
  override_data {
    target = data.aws_subnet.app["subnet-private-c"]
    values = { vpc_id = "vpc-other", availability_zone = "ap-northeast-2c", map_public_ip_on_launch = false }
  }
  expect_failures = [terraform_data.network_guard]
}

run "can_reuse_develop_app_subnet" {
  command = plan
  override_data {
    target = data.aws_subnet.app["subnet-private-a"]
    values = { vpc_id = "vpc-staging", availability_zone = "ap-northeast-2a", map_public_ip_on_launch = false, tags = { Environment = "develop" } }
  }
  assert {
    condition     = data.aws_subnet.app["subnet-private-a"].tags["Environment"] == "develop" && aws_autoscaling_group.application.desired_capacity == 0
    error_message = "공유 Subnet 재사용은 허용하되 검증 전 용량 0 유지."
  }
}

run "cannot_expose_alb_without_tls_and_clients" {
  command = plan
  variables { enable_alb = true }
  expect_failures = [aws_lb.backend[0]]
}

run "internal_alb_allows_only_router_private_ip" {
  command = plan
  variables {
    enable_alb                   = true
    alb_certificate_arn          = "arn:aws:acm:ap-northeast-2:000000000000:certificate/00000000-0000-0000-0000-000000000000"
    tailscale_router_instance_id = "i-0123456789abcdef0"
  }
  assert {
    condition     = aws_lb.backend[0].internal && toset(aws_lb.backend[0].subnets) == toset(var.app_subnet_ids)
    error_message = "ALB는 Private App 서브넷의 Internal 유형만 허용."
  }
  assert {
    condition     = length(aws_vpc_security_group_ingress_rule.alb_https) == 1 && aws_vpc_security_group_ingress_rule.alb_https[0].cidr_ipv4 == "10.20.0.28/32" && aws_vpc_security_group_ingress_rule.alb_https[0].from_port == 443 && aws_vpc_security_group_ingress_rule.alb_https[0].to_port == 443
    error_message = "진입 EC2의 Private IP 한 개/HTTPS만 허용해야 함."
  }
  assert {
    condition     = length(aws_ecs_service.backend) == 0
    error_message = "접근 기반만 준비하는 단계에서 앱 실행 금지."
  }
}

run "cannot_enable_alb_without_router" {
  command = plan
  variables {
    enable_alb          = true
    alb_certificate_arn = "arn:aws:acm:ap-northeast-2:000000000000:certificate/00000000-0000-0000-0000-000000000000"
  }
  expect_failures = [aws_lb.backend[0]]
}

run "cannot_use_router_from_other_vpc" {
  command = plan
  variables {
    enable_alb                   = true
    alb_certificate_arn          = "arn:aws:acm:ap-northeast-2:000000000000:certificate/00000000-0000-0000-0000-000000000000"
    tailscale_router_instance_id = "i-0123456789abcdef0"
  }
  override_data {
    target = data.aws_subnet.tailscale_router[0]
    values = { vpc_id = "vpc-other" }
  }
  expect_failures = [data.aws_subnet.tailscale_router[0]]
}

run "cannot_use_stopped_router" {
  command = plan
  variables {
    enable_alb                   = true
    alb_certificate_arn          = "arn:aws:acm:ap-northeast-2:000000000000:certificate/00000000-0000-0000-0000-000000000000"
    tailscale_router_instance_id = "i-0123456789abcdef0"
  }
  override_data {
    target = data.aws_instance.tailscale_router[0]
    values = { subnet_id = "subnet-public-a", instance_state = "stopped", private_ip = "10.20.0.28" }
  }
  expect_failures = [data.aws_instance.tailscale_router[0]]
}

run "legacy_public_client_allowlist_is_rejected" {
  command = plan
  variables { allowed_client_ipv4_cidrs = ["192.0.2.10/32"] }
  expect_failures = [var.allowed_client_ipv4_cidrs]
}

run "cannot_start_against_unverified_database" {
  command = plan
  variables {
    enable_alb                   = true
    enable_service               = true
    private_egress_verified      = true
    asg_min_size                 = 2
    asg_desired_capacity         = 2
    desired_task_count           = 2
    artifact_platform_verified   = true
    database_cutover_verified    = false
    alb_certificate_arn          = "arn:aws:acm:ap-northeast-2:000000000000:certificate/00000000-0000-0000-0000-000000000000"
    tailscale_router_instance_id = "i-0123456789abcdef0"
    backend_image                = "000000000000.dkr.ecr.ap-northeast-2.amazonaws.com/seonjalal/staging/backend@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    container_environment = {
      DB_URL               = "jdbc:mysql://verified-rds.invalid:3306/seonjalal_develop?sslMode=VERIFY_IDENTITY"
      DB_USERNAME          = "rehearsal"
      JWT_ISSUER           = "staging"
      AWS_REGION           = "ap-northeast-2"
      CORS_ALLOWED_ORIGINS = "https://staging.invalid"
      PRODUCT_IMAGE_BUCKET = "staging-example"
      AI_PROFILE_BASE_URL  = "http://ai.invalid:8000"
    }
    container_secrets = {
      DB_PASSWORD                         = { secret_arn = "arn:aws:secretsmanager:ap-northeast-2:000000000000:secret:staging-test", json_key = "DB_PASSWORD" }
      JWT_SECRET_BASE64                   = { secret_arn = "arn:aws:secretsmanager:ap-northeast-2:000000000000:secret:staging-test", json_key = "JWT_SECRET_BASE64" }
      LOGIN_RATE_LIMIT_HMAC_SECRET_BASE64 = { secret_arn = "arn:aws:secretsmanager:ap-northeast-2:000000000000:secret:staging-test", json_key = "LOGIN_RATE_LIMIT_HMAC_SECRET_BASE64" }
      PROFILING_SERVICE_TOKEN             = { secret_arn = "arn:aws:secretsmanager:ap-northeast-2:000000000000:secret:staging-test", json_key = "PROFILING_SERVICE_TOKEN" }
    }
  }
  expect_failures = [aws_ecs_task_definition.backend[0]]
}

run "verified_service_has_rolling_rollback" {
  command = plan
  variables {
    enable_alb                   = true
    enable_service               = true
    private_egress_verified      = true
    asg_min_size                 = 2
    asg_desired_capacity         = 2
    desired_task_count           = 2
    artifact_platform_verified   = true
    database_cutover_verified    = true
    alb_certificate_arn          = "arn:aws:acm:ap-northeast-2:000000000000:certificate/00000000-0000-0000-0000-000000000000"
    tailscale_router_instance_id = "i-0123456789abcdef0"
    backend_image                = "000000000000.dkr.ecr.ap-northeast-2.amazonaws.com/seonjalal/staging/backend@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    container_environment = {
      DB_URL               = "jdbc:mysql://verified-rds.invalid:3306/seonjalal_develop?sslMode=VERIFY_IDENTITY"
      DB_USERNAME          = "rehearsal"
      JWT_ISSUER           = "staging"
      AWS_REGION           = "ap-northeast-2"
      CORS_ALLOWED_ORIGINS = "https://staging.invalid"
      PRODUCT_IMAGE_BUCKET = "staging-example"
      AI_PROFILE_BASE_URL  = "http://ai.invalid:8000"
    }
    container_secrets = {
      DB_PASSWORD                         = { secret_arn = "arn:aws:secretsmanager:ap-northeast-2:000000000000:secret:staging-test", json_key = "DB_PASSWORD" }
      JWT_SECRET_BASE64                   = { secret_arn = "arn:aws:secretsmanager:ap-northeast-2:000000000000:secret:staging-test", json_key = "JWT_SECRET_BASE64" }
      LOGIN_RATE_LIMIT_HMAC_SECRET_BASE64 = { secret_arn = "arn:aws:secretsmanager:ap-northeast-2:000000000000:secret:staging-test", json_key = "LOGIN_RATE_LIMIT_HMAC_SECRET_BASE64" }
      PROFILING_SERVICE_TOKEN             = { secret_arn = "arn:aws:secretsmanager:ap-northeast-2:000000000000:secret:staging-test", json_key = "PROFILING_SERVICE_TOKEN" }
    }
  }
  assert {
    condition     = aws_ecs_service.backend[0].deployment_minimum_healthy_percent == 100 && aws_ecs_service.backend[0].deployment_maximum_percent == 200 && aws_ecs_service.backend[0].deployment_circuit_breaker[0].enable && aws_ecs_service.backend[0].deployment_circuit_breaker[0].rollback
    error_message = "기존정상태스크유지·후보추가기동·실패롤백 계약 위반."
  }
  assert {
    condition     = aws_ecs_service.backend[0].desired_count == 2 && aws_ecs_task_definition.backend[0].network_mode == "awsvpc"
    error_message = "Private 태스크두개 기준 위반."
  }
  assert {
    condition     = aws_ecs_task_definition.backend[0].skip_destroy
    error_message = "구버전 Revision을 등록 해제하지 않고 수동 롤백 기준으로 보존해야 함."
  }
}

run "verified_independent_fixture_can_start_without_claiming_cutover" {
  command = plan
  variables {
    enable_alb                   = true
    enable_service               = true
    enable_staging_certificate   = true
    private_egress_verified      = true
    asg_min_size                 = 2
    asg_desired_capacity         = 2
    desired_task_count           = 2
    artifact_platform_verified   = true
    database_cutover_verified    = false
    database_fixture_verified    = true
    alb_certificate_arn          = "arn:aws:acm:ap-northeast-2:000000000000:certificate/00000000-0000-0000-0000-000000000000"
    tailscale_router_instance_id = "i-0123456789abcdef0"
    backend_image                = "000000000000.dkr.ecr.ap-northeast-2.amazonaws.com/seonjalal/staging/backend@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    container_environment = {
      DB_URL               = "jdbc:mysql://fixture.invalid:3306/seonjalal_develop?sslMode=VERIFY_IDENTITY"
      DB_USERNAME          = "rehearsal"
      JWT_ISSUER           = "staging"
      AWS_REGION           = "ap-northeast-2"
      CORS_ALLOWED_ORIGINS = "https://staging.invalid"
      PRODUCT_IMAGE_BUCKET = "staging-example"
      AI_PROFILE_BASE_URL  = "http://ai.invalid:8000"
    }
    container_secrets = { for key in ["DB_PASSWORD", "JWT_SECRET_BASE64", "LOGIN_RATE_LIMIT_HMAC_SECRET_BASE64", "PROFILING_SERVICE_TOKEN"] : key => {
      secret_arn = "arn:aws:secretsmanager:ap-northeast-2:000000000000:secret:staging-runtime-test"
      json_key   = key
    } }
  }
  assert {
    condition     = !var.database_cutover_verified && var.database_fixture_verified && length(aws_ecs_task_definition.backend) == 1 && aws_ecs_service.backend[0].deployment_circuit_breaker[0].rollback
    error_message = "독립 Fixture와 실제 DB Cutover 완료를 혼동하지 않고 동일 앱 롤백 경계 유지."
  }
  assert {
    condition     = aws_route53_record.staging_backend[0].name == "v2-staging.seonjalal.com" && aws_route53_record.staging_backend[0].type == "A" && !aws_route53_record.staging_backend[0].allow_overwrite && aws_lb.backend[0].internal
    error_message = "전용 스테이징 이름만 Internal ALB에 연결하고 기존 DNS 덮어쓰기 금지."
  }
  assert {
    condition     = local.container.entryPoint == ["/bin/sh", "-c"] && strcontains(local.container.command[0], "exec java") && strcontains(local.container.command[0], "cp \"$JAVA_HOME/lib/security/cacerts\"") && length(regexall("-----BEGIN CERTIFICATE-----", [for env in local.container.environment : env.value if env.name == "STAGING_RDS_CA_BUNDLE"][0])) == 3
    error_message = "기본 Java Root 유지/RDS Root3개 추가/exec JVM 실행 계약 필요."
  }
}
