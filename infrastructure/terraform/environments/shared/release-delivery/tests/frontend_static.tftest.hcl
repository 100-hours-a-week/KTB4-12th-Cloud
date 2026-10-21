mock_provider "aws" {}

override_data {
  target = data.aws_ecr_repository.backend
  values = { arn = "arn:aws:ecr:ap-northeast-2:721744297924:repository/seonjalal/production/backend" }
}

run "no_frontend_static_roles_without_verified_distributions" {
  command = plan

  assert {
    condition     = length(aws_iam_role.frontend_static_deployer) == 0
    error_message = "Distribution ID 입력 전에는 FE 정적 배포 Role을 생성하지 않아야 합니다."
  }
}

run "environment_roles_are_isolated" {
  command = apply
  variables {
    frontend_static_targets = {
      staging    = "ESTAGING123"
      production = "EPRODUCTION123"
    }
  }

  assert {
    condition = (
      length(aws_iam_role.frontend_static_deployer) == 2 &&
      aws_iam_role.frontend_static_deployer["staging"].name == "seonjalal-v2-staging-frontend-deployer" &&
      aws_iam_role.frontend_static_deployer["production"].name == "seonjalal-v2-production-frontend-deployer"
    )
    error_message = "FE Workflow가 기대하는 환경별 Role 이름을 유지해야 합니다."
  }

  assert {
    condition = alltrue([
      for environment in ["staging", "production"] :
      strcontains(aws_iam_role.frontend_static_deployer[environment].assume_role_policy, "environment:fe-v2-${environment}") &&
      strcontains(aws_iam_role_policy.frontend_static_deployer[environment].policy, "frontend/${environment}/*") &&
      !strcontains(aws_iam_role_policy.frontend_static_deployer[environment].policy, "frontend/${environment == "staging" ? "production" : "staging"}/*") &&
      !strcontains(aws_iam_role_policy.frontend_static_deployer[environment].policy, "ecs:UpdateService") &&
      !strcontains(aws_iam_role_policy.frontend_static_deployer[environment].policy, "ssm:SendCommand")
    ])
    error_message = "FE 배포 신뢰·S3 쓰기 범위는 지정된 환경에만 한정해야 합니다."
  }
}

run "same_distribution_for_both_environments_is_rejected" {
  command = plan
  variables {
    frontend_static_targets = {
      staging    = "ESAME12345"
      production = "ESAME12345"
    }
  }
  expect_failures = [var.frontend_static_targets]
}
