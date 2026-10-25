mock_provider "aws" {}
mock_provider "aws" {
  alias = "us_east_1"
}

variables {
  production_distribution_arn = null
  staging_cloudfront_enabled  = false
  staging_gateway_ipv4        = null
}

run "private_versioned_bucket" {
  command = plan

  assert {
    condition     = aws_s3_bucket.frontend.bucket == "seonjalal-v2-frontend-721744297924-ap-northeast-2"
    error_message = "대상 버킷 이름 불일치"
  }
  assert {
    condition     = !aws_s3_bucket.frontend.force_destroy
    error_message = "파일 강제 삭제 금지"
  }
  assert {
    condition = (
      aws_s3_bucket_public_access_block.frontend.block_public_acls &&
      aws_s3_bucket_public_access_block.frontend.block_public_policy &&
      aws_s3_bucket_public_access_block.frontend.ignore_public_acls &&
      aws_s3_bucket_public_access_block.frontend.restrict_public_buckets
    )
    error_message = "S3 공개 접근 차단 네 항목 필수"
  }
  assert {
    condition     = one(aws_s3_bucket_ownership_controls.frontend.rule).object_ownership == "BucketOwnerEnforced"
    error_message = "ACL 기반 접근 금지"
  }
  assert {
    condition     = one(aws_s3_bucket_versioning.frontend.versioning_configuration).status == "Enabled"
    error_message = "이전 버전 보존 필수"
  }
  assert {
    condition     = !output.frontend.cloudfront_ready && !output.frontend.public_site_ready && !output.frontend.staging_vpn_ready
    error_message = "버킷 준비를 웹 서비스 공개 완료로 표현하면 안 됨"
  }
}

run "environment_prefixes_are_distinct" {
  command = plan
  assert {
    condition = (
      output.frontend.environment_prefixes.staging == "frontend/staging/" &&
      output.frontend.environment_prefixes.production == "frontend/production/" &&
      output.frontend.environment_prefixes.staging != output.frontend.environment_prefixes.production
    )
    error_message = "환경별 객체 경로 분리 필수. 이 검사는 실제 VPN 접근 검증이 아님."
  }
}

run "reject_other_account" {
  command = plan
  variables {
    aws_account_id = "000000000000"
  }
  expect_failures = [var.aws_account_id]
}

run "reject_other_region" {
  command = plan
  variables {
    aws_region = "us-east-1"
  }
  expect_failures = [var.aws_region]
}
