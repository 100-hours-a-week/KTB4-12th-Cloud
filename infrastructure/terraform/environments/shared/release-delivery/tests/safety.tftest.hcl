mock_provider "aws" {}
override_data {
  target = data.aws_ecr_repository.backend
  values = { arn = "arn:aws:ecr:ap-northeast-2:721744297924:repository/seonjalal/production/backend" }
}
run "publish_only_by_default" {
  command = plan
  assert {
    condition     = length(aws_iam_role.publisher) == 2 && length(aws_iam_role.deployer) == 0 && aws_ecr_repository.frontend.image_tag_mutability == "IMMUTABLE"
    error_message = "기본은자기ECR발행Role2개와불변FE저장소만준비. 배포권한자동생성금지."
  }
  assert {
    condition = alltrue([for role in values(aws_iam_role.publisher) :
      endswith(jsondecode(role.assume_role_policy).Statement[0].Condition.StringEquals["token.actions.githubusercontent.com:sub"], ":ref:refs/heads/main")
    ])
    error_message = "릴리스발행은해당앱Repository mainOIDC만허용."
  }
}
run "reject_wrong_environment_roles" {
  command = plan
  variables {
    deployment_targets = {
      staging = { cloudfront_distribution_id = "E123456789", task_role_arns = ["arn:aws:iam::721744297924:role/seonjalal-v2-production-task"], oidc_subject = "repo:100-hours-a-week/KTB4-12th-Cloud:environment:v2-staging" }
    }
  }
  expect_failures = [var.deployment_targets]
}
run "delivery_covers_both_bluegreen_slots_only" {
  command = apply
  variables {
    deployment_targets = {
      staging = { cloudfront_distribution_id = "E123456789", task_role_arns = ["arn:aws:iam::721744297924:role/seonjalal-v2-staging-task"], oidc_subject = "repo:100-hours-a-week/KTB4-12th-Cloud:environment:v2-staging" }
    }
  }
  assert {
    condition = alltrue([for name in ["main-backend", "payment-server", "main-backend-b", "payment-server-b"] :
      strcontains(aws_iam_role_policy.deployer["staging"].policy, "service/seonjalal-v2-staging-cluster/seonjalal-v2-staging-${name}\"") &&
      strcontains(aws_iam_role_policy.deployer["staging"].policy, "task-definition/seonjalal-v2-staging-${name}:*")
    ])
    error_message = "배포 Role은 블루-그린 두 슬롯(기본과 -b)의 서비스·Task Definition을 모두 갱신할 수 있어야 한다."
  }
  assert {
    condition     = !strcontains(aws_iam_role_policy.deployer["staging"].policy, "seonjalal-v2-production-") && !strcontains(aws_iam_role_policy.deployer["staging"].policy, "main-backend*")
    error_message = "다른 환경이나 와일드카드 서비스 이름으로 범위를 넓히면 안 된다."
  }
}
run "backend_deployer_reads_only_backend_repository" {
  command = apply
  variables {
    deployment_targets = {
      staging = { cloudfront_distribution_id = "E123456789", task_role_arns = ["arn:aws:iam::721744297924:role/seonjalal-v2-staging-task"], oidc_subject = "repo:100-hours-a-week/KTB4-12th-Cloud:environment:v2-staging" }
    }
  }
  assert {
    condition     = strcontains(aws_iam_role_policy.deployer["staging"].policy, "repository/seonjalal/production/backend") && !strcontains(aws_iam_role_policy.deployer["staging"].policy, "repository/seonjalal/production/frontend")
    error_message = "BE 배포 Role은 BE 이미지 저장소만 조회해야 한다(FE 저장소 권한 없음)."
  }
}
run "staging_delivery_is_scoped" {
  # mock_provider만 사용하므로 실제 AWS 생성 없이 계산된 ARN 포함 정책 검사.
  command = apply
  variables {
    deployment_targets = {
      staging = { cloudfront_distribution_id = "E123456789", task_role_arns = ["arn:aws:iam::721744297924:role/seonjalal-v2-staging-task"], oidc_subject = "repo:100-hours-a-week/KTB4-12th-Cloud:environment:v2-staging" }
    }
  }
  assert {
    condition     = strcontains(aws_iam_role_policy.deployer["staging"].policy, "frontend/staging/") && !strcontains(aws_iam_role_policy.deployer["staging"].policy, "frontend/production/") && !strcontains(aws_iam_role_policy.deployer["staging"].policy, "ssm:SendCommand")
    error_message = "StagingCD는운영S3/SSM서버명령권한을포함하면안됨."
  }
}
