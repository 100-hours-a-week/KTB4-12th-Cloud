mock_provider "aws" {
  mock_resource "aws_cloudfront_function" {
    defaults = {
      arn = "arn:aws:cloudfront::721744297924:function/mock-staging-viewer"
    }
  }
  mock_resource "aws_s3_bucket" {
    defaults = {
      arn = "arn:aws:s3:::mock-frontend"
    }
  }
  mock_resource "aws_cloudfront_distribution" {
    defaults = {
      arn = "arn:aws:cloudfront::721744297924:distribution/MOCKSTAGING"
    }
  }
}

mock_provider "aws" {
  alias = "us_east_1"
}

variables {
  production_distribution_arn = null
  staging_cloudfront_enabled  = true
  staging_gateway_ipv4        = "203.0.113.10"
}

run "production_read_preserves_staging" {
  command = apply
  variables {
    production_distribution_arn = "arn:aws:cloudfront::721744297924:distribution/MOCKPRODUCTION"
  }
  assert {
    condition = (
      length(jsondecode(aws_s3_bucket_policy.frontend_cloudfront[0].policy).Statement) == 2 &&
      jsondecode(aws_s3_bucket_policy.frontend_cloudfront[0].policy).Statement[0].Sid == "StagingCloudFrontReadOnly" &&
      jsondecode(aws_s3_bucket_policy.frontend_cloudfront[0].policy).Statement[0].Condition.StringEquals["AWS:SourceArn"] == aws_cloudfront_distribution.staging[0].arn &&
      jsondecode(aws_s3_bucket_policy.frontend_cloudfront[0].policy).Statement[1].Action == "s3:GetObject" &&
      jsondecode(aws_s3_bucket_policy.frontend_cloudfront[0].policy).Statement[1].Principal.Service == "cloudfront.amazonaws.com" &&
      jsondecode(aws_s3_bucket_policy.frontend_cloudfront[0].policy).Statement[1].Condition.StringEquals["AWS:SourceArn"] == var.production_distribution_arn &&
      endswith(jsondecode(aws_s3_bucket_policy.frontend_cloudfront[0].policy).Statement[1].Resource, "/frontend/production/*")
    )
    error_message = "Stage 허용 유지 및 정확한 운영 Distribution·환경 Prefix의 GetObject만 허용해야 한다."
  }
}

run "restricted_staging_distribution" {
  # ARN을 사용한 정책 검사까지 수행하는 Mock 적용이며 실제 AWS Apply가 아니다.
  command = apply
  assert {
    condition     = one(aws_cloudfront_distribution.staging[0].origin).origin_path == "/frontend/staging"
    error_message = "스테이징 Origin은 해당 Prefix만 사용해야 한다."
  }
  assert {
    condition = (
      one(aws_cloudfront_distribution.staging[0].origin).origin_access_control_id == aws_cloudfront_origin_access_control.staging[0].id &&
      length(one(aws_cloudfront_distribution.staging[0].origin).s3_origin_config) == 0
    )
    error_message = "OAC 연결을 유지하고 Provider가 생략하는 빈 OAI 블록은 선언하지 않아야 한다."
  }
  assert {
    condition = (
      one(aws_cloudfront_distribution.staging[0].default_cache_behavior).viewer_protocol_policy == "https-only" &&
      one(one(aws_cloudfront_distribution.staging[0].default_cache_behavior).function_association).event_type == "viewer-request"
    )
    error_message = "HTTPS와 매 요청 Viewer 허용 검사 필수."
  }
  assert {
    condition     = strcontains(aws_s3_bucket_policy.frontend_cloudfront[0].policy, "/frontend/staging/*") && !strcontains(aws_s3_bucket_policy.frontend_cloudfront[0].policy, "/frontend/production/")
    error_message = "운영 객체에 대한 읽기 권한 부여 금지."
  }
  assert {
    condition     = !output.frontend.staging_vpn_ready && !output.frontend.public_site_ready
    error_message = "자원 구성만으로 실제 VPN·운영 접속 검증 완료 표시 금지."
  }
}

run "reject_missing_gateway" {
  command = plan
  variables {
    staging_gateway_ipv4 = null
  }
  expect_failures = [aws_cloudfront_function.staging_viewer]
}

run "reject_cidr_gateway" {
  command = plan
  variables {
    staging_gateway_ipv4 = "0.0.0.0/0"
  }
  expect_failures = [var.staging_gateway_ipv4]
}
