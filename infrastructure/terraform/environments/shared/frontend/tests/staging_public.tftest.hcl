# 모든 AWS Provider 호출은 Mock 처리. 실제 계정/리소스 접근 없음.
mock_provider "aws" {
  mock_data "aws_lb" {
    defaults = {
      arn = "arn:aws:elasticloadbalancing:ap-northeast-2:721744297924:loadbalancer/app/mock/0123456789abcdef"
    }
  }
  mock_resource "aws_cloudfront_function" {
    defaults = {
      arn = "arn:aws:cloudfront::721744297924:function/mock-staging-public"
    }
  }
  mock_resource "aws_s3_bucket" {
    defaults = {
      arn = "arn:aws:s3:::mock-frontend"
    }
  }
  mock_resource "aws_cloudfront_distribution" {
    defaults = {
      arn            = "arn:aws:cloudfront::721744297924:distribution/MOCKPUBLIC"
      id             = "MOCKPUBLIC"
      domain_name    = "mockpublic.cloudfront.net"
      hosted_zone_id = "Z2FDTNDATAQYW2"
    }
  }
}
mock_provider "aws" {
  alias = "us_east_1"
  mock_resource "aws_acm_certificate" {
    defaults = {
      arn = "arn:aws:acm:us-east-1:721744297924:certificate/00000000-0000-0000-0000-000000000000"
      domain_validation_options = [{
        domain_name           = "v2-staging.seonjalal.com"
        resource_record_name  = "_mock.v2-staging.seonjalal.com."
        resource_record_type  = "CNAME"
        resource_record_value = "_mock.acm-validations.aws."
      }]
    }
  }
}

variables {
  production_distribution_arn = null
  staging_cloudfront_enabled  = false
  staging_gateway_ipv4        = null
}

run "public_distribution_is_off_by_default" {
  command = plan

  assert {
    condition = (
      length(aws_cloudfront_distribution.staging_public) == 0 &&
      length(aws_acm_certificate.staging_public) == 0 &&
      length(aws_route53_record.staging_public) == 0 &&
      length(aws_s3_bucket_policy.frontend_cloudfront) == 0
    )
    error_message = "공개 Distribution과 DNS는 변수를 켜기 전에는 만들지 않는다."
  }
}

run "public_distribution_serves_web_and_api_behind_gate" {
  # Mock 적용 검사이며 실제 AWS Apply가 아니다.
  command = apply
  variables {
    staging_public_enabled = true
    staging_public_zone_id = "ZEXAMPLE"
    staging_gate_token     = "abcdefghijklmnopqrstuvwxyz012345"
  }
  override_data {
    target = data.aws_route53_zone.staging_public[0]
    values = { name = "seonjalal.com.", private_zone = false }
  }

  assert {
    condition = (
      contains(aws_cloudfront_distribution.staging_public[0].aliases, "v2-staging.seonjalal.com") &&
      length(aws_cloudfront_distribution.staging_public[0].origin) == 2 &&
      length(aws_cloudfront_distribution.staging_public[0].ordered_cache_behavior) == 2 &&
      toset([for b in aws_cloudfront_distribution.staging_public[0].ordered_cache_behavior : b.path_pattern]) == toset(["/api/*", "/api"])
    )
    error_message = "별칭과 S3·API 두 원본, /api 두 동작이 있어야 한다."
  }
  assert {
    condition = (
      one(aws_cloudfront_distribution.staging_public[0].default_cache_behavior).viewer_protocol_policy == "redirect-to-https" &&
      length(one(aws_cloudfront_distribution.staging_public[0].default_cache_behavior).function_association) == 1 &&
      alltrue([for b in aws_cloudfront_distribution.staging_public[0].ordered_cache_behavior : length(b.function_association) == 1])
    )
    error_message = "모든 동작에 viewer-request 게이트 함수가 연결되어야 한다."
  }
  assert {
    condition = (
      one([for o in aws_cloudfront_distribution.staging_public[0].origin : o if o.origin_id == "staging-s3"]).origin_path == "/frontend/staging" &&
      one([for o in aws_cloudfront_distribution.staging_public[0].origin : o if o.origin_id == "staging-api"]).domain_name == "v2-staging-origin.seonjalal.com"
    )
    error_message = "화면 원본은 staging Prefix만, API 원본은 원본 이름만 사용해야 한다."
  }
  assert {
    condition = (
      length(jsondecode(aws_s3_bucket_policy.frontend_cloudfront[0].policy).Statement) == 1 &&
      jsondecode(aws_s3_bucket_policy.frontend_cloudfront[0].policy).Statement[0].Sid == "StagingPublicCloudFrontReadOnly" &&
      jsondecode(aws_s3_bucket_policy.frontend_cloudfront[0].policy).Statement[0].Condition.StringEquals["AWS:SourceArn"] == aws_cloudfront_distribution.staging_public[0].arn &&
      endswith(jsondecode(aws_s3_bucket_policy.frontend_cloudfront[0].policy).Statement[0].Resource, "/frontend/staging/*")
    )
    error_message = "공개 Distribution에 staging Prefix 읽기만 허용해야 한다."
  }
  assert {
    condition = (
      aws_route53_record.staging_public[0].name == "v2-staging.seonjalal.com" &&
      aws_route53_record.staging_public[0].type == "A" &&
      !aws_route53_record.staging_public[0].allow_overwrite
    )
    error_message = "사용자 접속 이름을 덮어쓰기 없이 CloudFront 별칭으로 연결해야 한다."
  }
}

run "gate_token_is_required" {
  command = plan
  variables {
    staging_public_enabled = true
    staging_public_zone_id = "ZEXAMPLE"
    staging_gate_token     = null
  }
  override_data {
    target = data.aws_route53_zone.staging_public[0]
    values = { name = "seonjalal.com.", private_zone = false }
  }
  expect_failures = [aws_cloudfront_function.staging_web]
}

run "short_gate_token_is_rejected" {
  command = plan
  variables {
    staging_gate_token = "short"
  }
  expect_failures = [var.staging_gate_token]
}

run "other_zone_is_rejected" {
  command = plan
  variables {
    staging_public_enabled = true
    staging_public_zone_id = "ZEXAMPLE"
    staging_gate_token     = "abcdefghijklmnopqrstuvwxyz012345"
  }
  override_data {
    target = data.aws_route53_zone.staging_public[0]
    values = { name = "other.invalid.", private_zone = false }
  }
  expect_failures = [aws_acm_certificate.staging_public]
}
