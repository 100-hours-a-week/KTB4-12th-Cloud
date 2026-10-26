# 모든 AWS Provider 호출은 Mock 처리. 실제 계정/백엔드/리소스 접근 없음.
mock_provider "aws" {
  mock_resource "aws_iam_instance_profile" {
    defaults = { arn = "arn:aws:iam::000000000000:instance-profile/mock" }
  }
  mock_resource "aws_launch_template" {
    defaults = { id = "lt-0123456789abcdef0", latest_version = 1 }
  }
  mock_resource "aws_autoscaling_group" {
    defaults = { arn = "arn:aws:autoscaling:ap-northeast-2:000000000000:autoScalingGroup:00000000-0000-0000-0000-000000000000:autoScalingGroupName/mock" }
  }
  mock_resource "aws_iam_role" {
    defaults = { arn = "arn:aws:iam::000000000000:role/mock" }
  }
  mock_resource "aws_lb" {
    defaults = { arn = "arn:aws:elasticloadbalancing:ap-northeast-2:000000000000:loadbalancer/app/mock/0123456789abcdef" }
  }
  mock_resource "aws_lb_target_group" {
    defaults = { arn = "arn:aws:elasticloadbalancing:ap-northeast-2:000000000000:targetgroup/mock/0123456789abcdef" }
  }
  mock_data "aws_instance" {
    defaults = {
      subnet_id      = "subnet-public-a"
      instance_state = "running"
      private_ip     = "10.20.0.28"
    }
  }
}

override_data {
  target = data.aws_ssm_parameter.ecs_optimized_ami
  values = { value = "ami-0123456789abcdef0" }
}
override_data {
  target = data.aws_subnet.app["subnet-private-a"]
  values = { vpc_id = "vpc-staging", availability_zone = "ap-northeast-2a", map_public_ip_on_launch = false }
}
override_data {
  target = data.aws_subnet.app["subnet-private-c"]
  values = { vpc_id = "vpc-staging", availability_zone = "ap-northeast-2c", map_public_ip_on_launch = false }
}
override_data {
  target = data.aws_subnet.public["subnet-public-a"]
  values = { vpc_id = "vpc-staging", availability_zone = "ap-northeast-2a", map_public_ip_on_launch = true }
}
override_data {
  target = data.aws_subnet.public["subnet-public-c"]
  values = { vpc_id = "vpc-staging", availability_zone = "ap-northeast-2c", map_public_ip_on_launch = true }
}
override_data {
  target = data.aws_subnet.tailscale_router[0]
  values = { vpc_id = "vpc-staging" }
}
override_resource {
  target = aws_ecr_repository.backend
  values = { repository_url = "000000000000.dkr.ecr.ap-northeast-2.amazonaws.com/seonjalal/staging/backend" }
}

variables {
  v1_candidate_secret_role_name      = null
  v1_cutover_network                 = null
  aws_account_id                     = "000000000000"
  aws_profile                        = null
  ecs_optimized_ami_id               = null
  vpc_id                             = "vpc-staging"
  app_subnet_ids                     = ["subnet-private-a", "subnet-private-c"]
  public_subnet_ids                  = ["subnet-public-a", "subnet-public-c"]
  secrets_endpoint_security_group_id = "sg-0123456789abcdef0"
  database_security_group_id         = "sg-0123456789abcdef1"
  tailscale_router_instance_id       = "i-0123456789abcdef0"
  alb_certificate_arn                = "arn:aws:acm:ap-northeast-2:000000000000:certificate/00000000-0000-0000-0000-000000000000"
  enable_staging_certificate         = false
  enable_runtime_probe               = false
  egress_probe_mode                  = false
  enable_alb                         = true
  enable_service                     = true
  private_egress_verified            = true
  asg_min_size                       = 0
  asg_desired_capacity               = 0
  asg_max_size                       = 4
  managed_scaling_enabled            = false
  desired_task_count                 = 0
  artifact_platform_verified         = true
  database_fixture_verified          = true
  database_cutover_verified          = false
  rds_tls_bootstrap_verified         = true
  inject_startup_failure             = false
  allowed_client_ipv4_cidrs          = []
  additional_tags                    = {}
  secret_kms_key_arns                = []
  product_image_bucket_arn           = null
  backend_image                      = "000000000000.dkr.ecr.ap-northeast-2.amazonaws.com/seonjalal/staging/backend@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
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

run "ai_host_is_not_created_by_default" {
  command = plan
  assert {
    condition = (
      length(aws_launch_template.ai) == 0 &&
      length(aws_autoscaling_group.ai) == 0 &&
      length(aws_ecs_capacity_provider.ai) == 0 &&
      aws_ecs_cluster_capacity_providers.this.capacity_providers == toset([aws_ecs_capacity_provider.application.name])
    )
    error_message = "스위치가 꺼져 있으면 AI 호스트를 만들지 않고 클러스터는 BE 공급자만 연결해야 한다."
  }
}

run "ai_host_matches_production_shape_and_stays_off" {
  command = apply
  variables {
    ai_host_enabled = true
  }
  assert {
    condition = (
      aws_launch_template.ai[0].instance_type == "t3.medium" &&
      aws_launch_template.ai[0].metadata_options[0].http_tokens == "required" &&
      tostring(one(aws_launch_template.ai[0].block_device_mappings).ebs[0].encrypted) == "true" &&
      tostring(one(aws_launch_template.ai[0].block_device_mappings).ebs[0].volume_size) == "30" &&
      strcontains(base64decode(aws_launch_template.ai[0].user_data), "ECS_RESERVED_MEMORY=512") &&
      strcontains(base64decode(aws_launch_template.ai[0].user_data), "ECS_AWSVPC_BLOCK_IMDS=true")
    )
    error_message = "운영 AI 호스트와 같은 t3.medium, IMDSv2, 암호화 볼륨, 예약 메모리 512를 써야 한다."
  }
  assert {
    condition = (
      aws_autoscaling_group.ai[0].min_size == 0 &&
      aws_autoscaling_group.ai[0].max_size == 1 &&
      aws_autoscaling_group.ai[0].desired_capacity == 0 &&
      aws_autoscaling_group.ai[0].vpc_zone_identifier == toset(["subnet-private-a"])
    )
    error_message = "기본은 꺼짐(desired 0), 최대 1대, 서울 A 존 Private App Subnet 하나여야 한다."
  }
}

run "ai_capacity_provider_is_manual_and_attached_next_to_application" {
  command = apply
  variables {
    ai_host_enabled = true
  }
  assert {
    condition = (
      endswith(aws_ecs_capacity_provider.ai[0].name, "-ai") &&
      aws_ecs_capacity_provider.ai[0].auto_scaling_group_provider[0].managed_scaling[0].status == "DISABLED" &&
      aws_ecs_capacity_provider.ai[0].auto_scaling_group_provider[0].managed_termination_protection == "DISABLED" &&
      aws_ecs_cluster_capacity_providers.this.capacity_providers == toset([aws_ecs_capacity_provider.application.name, aws_ecs_capacity_provider.ai[0].name])
    )
    error_message = "AI 공급자는 자동 확장 없이 BE 공급자 옆에 연결되어야 한다."
  }
  assert {
    condition     = one(aws_ecs_cluster_capacity_providers.this.default_capacity_provider_strategy).capacity_provider == aws_ecs_capacity_provider.application.name
    error_message = "기본 전략은 BE 공급자 그대로여야 한다."
  }
}

run "ai_host_can_be_turned_on_with_one_instance" {
  command = apply
  variables {
    ai_host_enabled     = true
    ai_desired_capacity = 1
  }
  assert {
    condition     = aws_autoscaling_group.ai[0].desired_capacity == 1
    error_message = "AI 호스트 1대를 허용해야 한다."
  }
}

run "ai_host_does_not_change_the_be_host_group" {
  command = apply
  variables {
    ai_host_enabled = true
  }
  assert {
    condition = (
      aws_autoscaling_group.application.desired_capacity == 0 &&
      aws_autoscaling_group.application.max_size == 4 &&
      length(aws_autoscaling_group.application.suspended_processes) == 6
    )
    error_message = "AI 호스트 추가가 BE 호스트 ASG(휴면 설정 포함)를 바꾸면 안 된다."
  }
}

run "two_ai_hosts_are_rejected" {
  command = plan
  variables {
    ai_host_enabled     = true
    ai_desired_capacity = 2
  }
  expect_failures = [var.ai_desired_capacity]
}

run "ai_capacity_without_switch_is_rejected" {
  command = plan
  variables {
    ai_host_enabled     = false
    ai_desired_capacity = 1
  }
  expect_failures = [var.ai_desired_capacity]
}
