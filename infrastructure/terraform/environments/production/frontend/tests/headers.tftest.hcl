mock_provider "aws" {
  mock_data "aws_s3_bucket" {
    defaults = {
      arn                         = "arn:aws:s3:::mock-frontend"
      bucket_regional_domain_name = "mock-frontend.s3.ap-northeast-2.amazonaws.com"
    }
  }
  mock_data "aws_lb" {
    defaults = {
      arn             = "arn:aws:elasticloadbalancing:ap-northeast-2:721744297924:loadbalancer/app/mock/1234567890abcdef"
      internal        = true
      security_groups = ["sg-0123456789abcdef0"]
    }
  }
  mock_data "aws_lb_listener" {
    defaults = {
      arn = "arn:aws:elasticloadbalancing:ap-northeast-2:721744297924:listener/app/mock/1234567890abcdef/1234567890abcdef"
    }
  }
  mock_data "aws_ec2_managed_prefix_list" {
    defaults = {
      id = "pl-0123456789abcdef0"
    }
  }
  mock_resource "aws_cloudfront_function" {
    defaults = {
      arn = "arn:aws:cloudfront::721744297924:function/mock-function"
    }
  }
  mock_resource "aws_cloudfront_vpc_origin" {
    defaults = {
      id = "vo_0123456789abcdef0"
    }
  }
  mock_resource "aws_cloudfront_origin_access_control" {
    defaults = {
      id = "E0123456789ABC"
    }
  }
  mock_resource "aws_cloudfront_response_headers_policy" {
    defaults = {
      id = "11111111-2222-3333-4444-555555555555"
    }
  }
}

variables {
  aws_account_id         = "721744297924"
  candidate_write_gate   = false
  enable_frontend_upload = false
}

run "security_headers_are_set_and_override_origin" {
  command = apply
  assert {
    condition = (
      aws_cloudfront_response_headers_policy.frontend_security.security_headers_config[0].frame_options[0].frame_option == "SAMEORIGIN" &&
      aws_cloudfront_response_headers_policy.frontend_security.security_headers_config[0].frame_options[0].override == true &&
      aws_cloudfront_response_headers_policy.frontend_security.security_headers_config[0].content_security_policy[0].content_security_policy == "frame-ancestors 'self'" &&
      aws_cloudfront_response_headers_policy.frontend_security.security_headers_config[0].content_security_policy[0].override == true &&
      aws_cloudfront_response_headers_policy.frontend_security.security_headers_config[0].content_type_options[0].override == true &&
      aws_cloudfront_response_headers_policy.frontend_security.security_headers_config[0].referrer_policy[0].referrer_policy == "strict-origin-when-cross-origin" &&
      aws_cloudfront_response_headers_policy.frontend_security.security_headers_config[0].referrer_policy[0].override == true
    )
    error_message = "X-Frame-Options, CSP frame-ancestors, nosniff, Referrer-Policy가 이슈 제안 값이고 원본 값보다 우선해야 한다."
  }
}

run "permissions_policy_defaults_to_blocking_camera_microphone_geolocation" {
  command = apply
  assert {
    condition = (
      length(aws_cloudfront_response_headers_policy.frontend_security.custom_headers_config) == 1 &&
      one(aws_cloudfront_response_headers_policy.frontend_security.custom_headers_config[0].items).header == "Permissions-Policy" &&
      one(aws_cloudfront_response_headers_policy.frontend_security.custom_headers_config[0].items).value == "camera=(), microphone=(), geolocation=()" &&
      one(aws_cloudfront_response_headers_policy.frontend_security.custom_headers_config[0].items).override == true
    )
    error_message = "기본 Permissions-Policy는 카메라·마이크·위치를 막아야 한다."
  }
}

run "permissions_policy_can_be_disabled" {
  command = apply
  variables {
    permissions_policy = null
  }
  assert {
    condition     = length(aws_cloudfront_response_headers_policy.frontend_security.custom_headers_config) == 0
    error_message = "값이 null이면 Permissions-Policy 헤더를 보내지 않아야 한다."
  }
}

run "hsts_is_not_part_of_this_change" {
  command = apply
  assert {
    condition     = length(aws_cloudfront_response_headers_policy.frontend_security.security_headers_config[0].strict_transport_security) == 0
    error_message = "되돌리기 어려운 HSTS는 이 변경에 포함하지 않는다."
  }
}

run "policy_is_attached_to_static_behavior_only" {
  command = apply
  assert {
    condition = (
      aws_cloudfront_distribution.production.default_cache_behavior[0].response_headers_policy_id == aws_cloudfront_response_headers_policy.frontend_security.id &&
      alltrue([for b in aws_cloudfront_distribution.production.ordered_cache_behavior :
        try(b.response_headers_policy_id, "") == "" || try(b.response_headers_policy_id, "") == null
      ])
    )
    error_message = "정적 파일 기본 동작에만 연결하고 API 동작에는 붙이지 않아야 한다."
  }
}

run "caching_and_function_settings_are_unchanged" {
  command = apply
  assert {
    condition = (
      aws_cloudfront_distribution.production.default_cache_behavior[0].cache_policy_id == "4135ea2d-6df8-44a3-9df3-4b5a84be39ad" &&
      aws_cloudfront_distribution.production.default_cache_behavior[0].target_origin_id == "production-s3" &&
      toset(aws_cloudfront_distribution.production.aliases) == toset(["www.seonjalal.com"]) &&
      length(aws_cloudfront_distribution.production.default_cache_behavior[0].function_association) == 1
    )
    error_message = "캐시 정책, 원본, 별칭, SPA 함수 연결은 이 변경으로 달라지면 안 된다."
  }
}

run "rejects_empty_permissions_policy" {
  command = plan
  variables {
    permissions_policy = "  "
  }
  expect_failures = [var.permissions_policy]
}

run "rejects_newline_in_permissions_policy" {
  command = plan
  variables {
    permissions_policy = "camera=()\nX-Injected: 1"
  }
  expect_failures = [var.permissions_policy]
}
