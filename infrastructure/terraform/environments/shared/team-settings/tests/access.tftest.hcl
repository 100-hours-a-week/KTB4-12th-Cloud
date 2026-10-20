mock_provider "aws" {}

run "settings_admin_keeps_mfa_without_server_access" {
  command = plan
  variables {
    team_settings_admin_enabled = true
  }
  assert {
    condition = alltrue([for policy in values(aws_iam_group_policy.settings) :
      alltrue([for statement in jsondecode(policy.policy).Statement :
        statement.Condition.Bool["aws:MultiFactorAuthPresent"] == "true" &&
        statement.Condition.StringEquals["aws:RequestedRegion"] == "ap-northeast-2"
      ])
    ])
    error_message = "설정 관리자 모드도 MFA·서울 리전 조건 유지 필요."
  }
  assert {
    condition = alltrue([for policy in values(aws_iam_group_policy.settings) :
      jsondecode(policy.policy).Statement[0].Action == "secretsmanager:*" &&
      !contains(jsondecode(policy.policy).Statement[2].Action, "ssm:SendCommand") &&
      !contains(jsondecode(policy.policy).Statement[2].Action, "ssm:StartSession")
    ])
    error_message = "설정 관리 권한에 서버 조작 권한 포함 금지."
  }
  assert {
    condition     = output.team_settings.settings_admin_enabled && length(aws_iam_group_policy.settings) == 2
    error_message = "기존2팀 그룹 설정 정책만 관리자 모드 적용 필요."
  }
}

run "team_settings_are_scoped" {
  command = plan
  assert {
    condition     = length(aws_iam_user.member) == 4 && !contains(keys(aws_iam_user.member), "fs-theo")
    error_message = "새 개인 계정4개만 생성하고 기존Theo계정은 조회만 수행 필요."
  }
  assert {
    condition     = length(aws_secretsmanager_secret.team) == 4 && alltrue([for store in values(aws_secretsmanager_secret.team) : !strcontains(store.name, "production")])
    error_message = "Develop/Staging 팀별 빈Secret4개만 허용."
  }
  assert {
    condition     = alltrue([for policy in values(aws_iam_group_policy.settings) : jsondecode(policy.policy).Statement[0].Condition.Bool["aws:MultiFactorAuthPresent"] == "true"])
    error_message = "팀 설정 값 접근은 MFA 필수."
  }
  assert {
    condition     = alltrue([for team in keys(local.teams) : length(local.secret_resources[team]) == 2 && length(local.parameter_resources[team]) == 2])
    error_message = "각 팀은 자신의 비운영2개 환경 경로에만 접근 허용 필요."
  }
  assert {
    condition     = !output.team_settings.runtime_connected && !output.team_settings.theo_admin_removed && !output.team_settings.login_profiles_ready
    error_message = "기반생성만으로권한회수·로그인·실행주입완료표시금지."
  }
  assert {
    condition = alltrue(flatten([for team in keys(local.teams) : [
      for resource in concat(local.secret_resources[team], local.parameter_resources[team]) :
      !strcontains(resource, "production") && !strcontains(resource, "/release/") &&
      strcontains(resource, "/${team}")
    ]]))
    error_message = "운영/배포기준값/타팀리소스에Allow범위확대금지."
  }
  assert {
    condition = alltrue([for policy in values(aws_iam_group_policy.settings) :
      jsondecode(policy.policy).Statement[2].Effect == "Deny" &&
      jsondecode(policy.policy).Statement[3].Effect == "Deny" &&
      !contains(jsondecode(policy.policy).Statement[0].Action, "secretsmanager:DeleteSecret")
    ])
    error_message = "다른Secret/Parameter리소스명시차단및비밀삭제권한미부여필요."
  }
}
