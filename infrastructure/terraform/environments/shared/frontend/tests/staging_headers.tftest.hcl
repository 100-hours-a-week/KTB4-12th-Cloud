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
  mock_resource "aws_cloudfront_response_headers_policy" {
    defaults = {
      id = "11111111-2222-3333-4444-555555555555"
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

run "policy_is_not_created_until_public_distribution_is_enabled" {
  command = plan
  assert {
    condition     = length(aws_cloudfront_response_headers_policy.staging_frontend_security) == 0
    error_message = "공개 Distribution을 켜기 전에는 헤더 정책도 만들지 않는다."
  }
}

run "staging_headers_match_production_values" {
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
      aws_cloudfront_response_headers_policy.staging_frontend_security[0].security_headers_config[0].frame_options[0].frame_option == "SAMEORIGIN" &&
      aws_cloudfront_response_headers_policy.staging_frontend_security[0].security_headers_config[0].content_security_policy[0].content_security_policy == "frame-ancestors 'self'" &&
      aws_cloudfront_response_headers_policy.staging_frontend_security[0].security_headers_config[0].referrer_policy[0].referrer_policy == "strict-origin-when-cross-origin" &&
      aws_cloudfront_response_headers_policy.staging_frontend_security[0].security_headers_config[0].frame_options[0].override == true &&
      aws_cloudfront_response_headers_policy.staging_frontend_security[0].security_headers_config[0].content_type_options[0].override == true &&
      one(aws_cloudfront_response_headers_policy.staging_frontend_security[0].custom_headers_config[0].items).header == "Permissions-Policy" &&
      one(aws_cloudfront_response_headers_policy.staging_frontend_security[0].custom_headers_config[0].items).value == "camera=(), microphone=(), geolocation=()" &&
      length(aws_cloudfront_response_headers_policy.staging_frontend_security[0].security_headers_config[0].strict_transport_security) == 0
    )
    error_message = "스테이징 헤더 값은 운영과 같아야 하고 HSTS는 포함하지 않는다."
  }
}

run "policy_is_attached_to_static_behavior_only_and_gate_is_kept" {
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
      one(aws_cloudfront_distribution.staging_public[0].default_cache_behavior).response_headers_policy_id == aws_cloudfront_response_headers_policy.staging_frontend_security[0].id &&
      alltrue([for b in aws_cloudfront_distribution.staging_public[0].ordered_cache_behavior :
        try(b.response_headers_policy_id, "") == "" || try(b.response_headers_policy_id, "") == null
      ])
    )
    error_message = "정적 파일 기본 동작에만 연결하고 API 동작에는 붙이지 않아야 한다."
  }
  assert {
    condition = (
      length(one(aws_cloudfront_distribution.staging_public[0].default_cache_behavior).function_association) == 1 &&
      alltrue([for b in aws_cloudfront_distribution.staging_public[0].ordered_cache_behavior : length(b.function_association) == 1]) &&
      one(aws_cloudfront_distribution.staging_public[0].default_cache_behavior).cache_policy_id == "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"
    )
    error_message = "접근 게이트 함수와 캐시 비활성 정책은 변하지 않아야 한다."
  }
}

run "permissions_policy_can_be_disabled" {
  command = apply
  variables {
    staging_public_enabled = true
    staging_public_zone_id = "ZEXAMPLE"
    staging_gate_token     = "abcdefghijklmnopqrstuvwxyz012345"
    permissions_policy     = null
  }
  override_data {
    target = data.aws_route53_zone.staging_public[0]
    values = { name = "seonjalal.com.", private_zone = false }
  }
  assert {
    condition     = length(aws_cloudfront_response_headers_policy.staging_frontend_security[0].custom_headers_config) == 0
    error_message = "값이 null이면 Permissions-Policy를 보내지 않아야 한다."
  }
}

run "rejects_newline_in_permissions_policy" {
  command = plan
  variables {
    permissions_policy = "camera=()\nX-Injected: 1"
  }
  expect_failures = [var.permissions_policy]
}
