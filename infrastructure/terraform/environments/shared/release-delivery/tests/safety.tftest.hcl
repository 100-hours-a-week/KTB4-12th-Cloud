mock_provider "aws" {}
override_data {
  target = data.aws_ecr_repository.backend
  values = { arn = "arn:aws:ecr:ap-northeast-2:721744297924:repository/seonjalal/production/backend" }
}
run "publish_only_by_default" {
  command = plan
  assert {
    condition     = length(aws_iam_role.publisher) == 2 && length(aws_iam_role.deployer) == 0 && length(aws_iam_role.frontend_deployer) == 0
    error_message = "기본은 발행 Role 2개만 준비, 배포 Role은 명시 입력 전 생성 금지."
  }
  assert {
    condition     = !strcontains(aws_iam_role_policy.frontend_publisher.policy, "ecr:") && !strcontains(aws_iam_role_policy.frontend_publisher.policy, "/frontend/staging") && strcontains(aws_iam_role_policy.frontend_publisher.policy, "/releases/frontend/")
    error_message = "FE 발행은 정적 릴리스 경로만 허용, ECR·서비스 경로 권한 금지."
  }
}
run "reject_wrong_environment_roles" {
  command = plan
  variables {
    deployment_targets = {
      staging = { task_role_arns = ["arn:aws:iam::721744297924:role/seonjalal-v2-production-task"], oidc_subject = "repo:100-hours-a-week/KTB4-12th-Cloud:environment:v2-staging" }
    }
  }
  expect_failures = [var.deployment_targets]
}
run "backend_delivery_has_no_frontend_permissions" {
  # mock Provider만 사용하며 실제 AWS 쓰기 없음.
  command = apply
  variables {
    deployment_targets = {
      staging = { task_role_arns = ["arn:aws:iam::721744297924:role/seonjalal-v2-staging-task"], oidc_subject = "repo:100-hours-a-week/KTB4-12th-Cloud:environment:v2-staging" }
    }
  }
  assert {
    condition     = !strcontains(aws_iam_role_policy.deployer["staging"].policy, "s3:") && !strcontains(aws_iam_role_policy.deployer["staging"].policy, "cloudfront:") && !strcontains(aws_iam_role_policy.deployer["staging"].policy, "ssm:SendCommand")
    error_message = "Cloud BE 배포 Role에는 FE·SSM 권한 없음."
  }
}
run "frontend_delivery_is_environment_scoped" {
  command = apply
  variables {
    frontend_deployment_targets = { staging = { cloudfront_distribution_id = "E123456789" } }
  }
  assert {
    condition     = strcontains(aws_iam_role_policy.frontend_deployer["staging"].policy, "/frontend/staging/") && !strcontains(aws_iam_role_policy.frontend_deployer["staging"].policy, "/frontend/production/") && !strcontains(aws_iam_role_policy.frontend_deployer["staging"].policy, "ecs:") && endswith(jsondecode(aws_iam_role.frontend_deployer["staging"].assume_role_policy).Statement[0].Condition.StringEquals["token.actions.githubusercontent.com:sub"], ":environment:fe-v2-staging")
    error_message = "FE 레포 환경 Identity·S3 경로 한정 권한 필요."
  }
}
