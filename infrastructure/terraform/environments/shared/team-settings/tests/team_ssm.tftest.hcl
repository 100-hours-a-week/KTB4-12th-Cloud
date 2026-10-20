# 실제 AWS 호출 없이 opt-in·기존 MFA/설정 권한 보존 검사.
mock_provider "aws" {}

run "disabled_by_default" {
  command = plan
  assert {
    condition     = length(aws_iam_policy.team_ssm_access) == 0 && length(aws_iam_group_policy_attachment.team_ssm_access) == 0
    error_message = "명시 활성화 전 SSM 정책 생성/연결 금지."
  }
}

run "enabled_for_both_existing_teams" {
  command = plan
  variables {
    team_settings_admin_enabled = true
    team_ssm_access_enabled     = true
  }
  assert {
    condition     = length(aws_iam_policy.team_ssm_access) == 1 && toset(keys(aws_iam_group_policy_attachment.team_ssm_access)) == toset(["fullstack", "ai"])
    error_message = "공통 관리형 정책 하나를 기존 두 그룹에만 연결 필요."
  }
  assert {
    condition     = output.team_ssm_access.enabled && sum([for members in values(local.teams) : length(members)]) == 5
    error_message = "기존 팀원 5명만 대상."
  }
  assert {
    condition = alltrue([for policy in values(aws_iam_group_policy.settings) :
      jsondecode(policy.policy) == jsondecode(file("${path.module}/settings-admin-policy.json"))
    ])
    error_message = "실제 적용된 설정 관리자 권한 변경 금지."
  }
  assert {
    condition = alltrue([for policy in values(aws_iam_group_policy.self_service) :
      jsondecode(policy.policy).Statement[3].Effect == "Deny" &&
      jsondecode(policy.policy).Statement[3].Condition.BoolIfExists["aws:MultiFactorAuthPresent"] == "false"
    ])
    error_message = "기존 MFA 미인증 차단 보존 필요."
  }
  assert {
    condition = alltrue([for statement in jsondecode(aws_iam_policy.team_ssm_access[0].policy).Statement :
      statement.Effect != "Allow" || try(statement.Condition.Bool["aws:MultiFactorAuthPresent"], "false") == "true"
    ])
    error_message = "CLI 로그인 추가를 포함한 모든 Allow에 기존 MFA 조건 유지 필요."
  }
}

run "reject_conflicting_settings_mode" {
  command = plan
  variables {
    team_settings_admin_enabled = false
    team_ssm_access_enabled     = true
  }
  expect_failures = [aws_iam_policy.team_ssm_access[0]]
}
