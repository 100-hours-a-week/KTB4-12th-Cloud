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

run "origin_name_is_off_by_default" {
  command = plan

  assert {
    condition = (
      length(aws_acm_certificate.staging_origin) == 0 &&
      length(aws_lb_listener_certificate.staging_origin) == 0 &&
      length(aws_route53_record.staging_origin) == 0 &&
      length(aws_vpc_security_group_ingress_rule.alb_https_cloudfront_vpc_origin) == 0
    )
    error_message = "공개 경로 준비 리소스는 변수를 켜기 전에는 만들지 않는다."
  }
}

run "origin_name_adds_certificate_dns_and_listener_certificate_only" {
  command = plan
  variables {
    enable_staging_certificate = true
    enable_staging_origin_name = true
    staging_public_zone_id     = "ZEXAMPLE"
  }
  override_data {
    target = data.aws_route53_zone.staging_public[0]
    values = { name = "seonjalal.com.", private_zone = false }
  }

  assert {
    condition = (
      aws_acm_certificate.staging_origin[0].domain_name == "v2-staging-origin.seonjalal.com" &&
      aws_acm_certificate.staging_origin[0].validation_method == "DNS" &&
      aws_acm_certificate.staging[0].domain_name == "v2-staging.seonjalal.com"
    )
    error_message = "원본 이름 인증서를 추가하고 기존 v2-staging 인증서는 유지한다."
  }
  assert {
    condition = (
      aws_route53_record.staging_origin_certificate_validation[0].type == "CNAME" &&
      !aws_route53_record.staging_origin_certificate_validation[0].allow_overwrite &&
      aws_route53_record.staging_origin[0].name == "v2-staging-origin.seonjalal.com" &&
      aws_route53_record.staging_origin[0].type == "A" &&
      !aws_route53_record.staging_origin[0].allow_overwrite &&
      aws_route53_record.staging_backend[0].name == "v2-staging.seonjalal.com"
    )
    error_message = "새 레코드만 만들고 덮어쓰기는 금지한다. 사용자 접속 이름(v2-staging)은 이 단계에서 바꾸지 않는다."
  }
  assert {
    condition     = length(aws_lb_listener_certificate.staging_origin) == 1 && length(aws_vpc_security_group_ingress_rule.alb_https_cloudfront_vpc_origin) == 0
    error_message = "리스너 인증서만 추가하고 ALB 접근 규칙은 별도 변수로 분리한다."
  }
}

run "cloudfront_access_references_service_security_group_not_prefix_list" {
  command = plan
  variables {
    enable_cloudfront_origin_access = true
  }

  assert {
    condition = (
      aws_vpc_security_group_ingress_rule.alb_https_cloudfront_vpc_origin[0].referenced_security_group_id != null &&
      aws_vpc_security_group_ingress_rule.alb_https_cloudfront_vpc_origin[0].prefix_list_id == null &&
      aws_vpc_security_group_ingress_rule.alb_https_cloudfront_vpc_origin[0].cidr_ipv4 == null &&
      aws_vpc_security_group_ingress_rule.alb_https_cloudfront_vpc_origin[0].from_port == 443 &&
      aws_vpc_security_group_ingress_rule.alb_https_cloudfront_vpc_origin[0].to_port == 443
    )
    error_message = "443은 CloudFront VPC Origin 서비스 SG 참조 1건만 허용한다(접두사 목록은 SG 규칙 한도 초과)."
  }
}
