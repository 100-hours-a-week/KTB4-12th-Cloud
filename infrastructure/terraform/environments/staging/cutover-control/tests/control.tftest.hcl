# 사용자 terraform test용 모의 Plan. 실제 AWS 실행 없음.
mock_provider "aws" {}

override_data {
  target = data.aws_instance.source
  values = {
    id                   = "i-0c6f5ea09b5e20859"
    private_ip           = "172.31.6.212"
    instance_state       = "running"
    iam_instance_profile = "source-profile"
  }
}

override_data {
  target = data.aws_iam_instance_profile.source
  values = {
    role_arn = "arn:aws:iam::721744297924:role/seonjalal-v1-develop-role"
  }
}

run "only_dedicated_inline_policy" {
  command = plan
  assert {
    condition = (
      aws_iam_role_policy.cutover_control.role == "seonjalal-v1-develop-role" &&
      jsondecode(aws_iam_role_policy.cutover_control.policy).Statement[1].Resource == local.task_arn &&
      output.cutover_control.task_stop_executed == false
    )
    error_message = "비운영 고정 Role·새 Task 한정 권한만 추가하고 실제 정지하지 않아야 함."
  }
}

run "wrong_source_ip_blocks_policy" {
  command = plan
  override_data {
    target = data.aws_instance.source
    values = {
      id                   = "i-0c6f5ea09b5e20859"
      private_ip           = "172.31.0.99"
      instance_state       = "running"
      iam_instance_profile = "source-profile"
    }
  }
  expect_failures = [aws_iam_role_policy.cutover_control]
}

run "wrong_profile_role_blocks_policy" {
  command = plan
  override_data {
    target = data.aws_iam_instance_profile.source
    values = { role_arn = "arn:aws:iam::721744297924:role/other-role" }
  }
  expect_failures = [aws_iam_role_policy.cutover_control]
}
