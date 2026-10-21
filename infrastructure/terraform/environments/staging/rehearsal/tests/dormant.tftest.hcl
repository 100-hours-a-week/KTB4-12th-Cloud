# 모든 AWS Provider 호출은 Mock 처리. 실제 계정/백엔드/리소스 접근 없음.
mock_provider "aws" {
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

run "dormant_service_keeps_topology_without_compute" {
  command = plan

  assert {
    condition = (
      length(aws_ecs_task_definition.backend) == 1 &&
      length(aws_ecs_service.backend) == 1 &&
      aws_ecs_service.backend[0].desired_count == 0 &&
      aws_autoscaling_group.application.min_size == 0 &&
      aws_autoscaling_group.application.desired_capacity == 0 &&
      !var.managed_scaling_enabled
    )
    error_message = "휴면 스테이징은 Task Definition과 Service를 보존하되 Task/EC2 실행 용량이 0이어야 함."
  }
}

run "dormant_service_rejects_running_host" {
  command = plan

  variables {
    asg_desired_capacity = 1
  }

  expect_failures = [aws_ecs_service.backend[0]]
}

run "active_service_rejects_single_task" {
  command = plan

  variables {
    desired_task_count = 1
  }

  expect_failures = [var.desired_task_count]
}
