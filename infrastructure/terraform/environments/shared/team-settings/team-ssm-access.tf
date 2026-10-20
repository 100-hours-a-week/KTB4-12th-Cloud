variable "team_ssm_access_enabled" {
  type        = bool
  default     = false
  description = "MFA 인증 팀원에게 접근 태그가 있는 Develop/Staging EC2의 기본 SSM 셸만 허용."
}

# 팀별 설정 정책과 분리한 공통 관리형 정책. 기존 Secret 권한은 변경하지 않음.
resource "aws_iam_policy" "team_ssm_access" {
  count       = var.team_ssm_access_enabled ? 1 : 0
  name        = "SeonjalalTeamNonproductionSessionAccess"
  description = "MFA shell sessions on explicitly tagged nonproduction application instances only"
  policy      = file("${path.module}/team-ssm-policy.json")

  lifecycle {
    precondition {
      # 현재 실제 적용된 admin 모드만 지원. 기본 모드의 ssm:* 명시 Deny 우회 금지.
      condition     = var.team_settings_admin_enabled
      error_message = "SSM 활성화에는 현재 적용된 team_settings_admin_enabled=true 유지 필요. 기본 설정 정책의 명시 Deny 조정은 별도 검토 대상."
    }
  }
}

resource "aws_iam_group_policy_attachment" "team_ssm_access" {
  for_each   = var.team_ssm_access_enabled ? local.teams : {}
  group      = aws_iam_group.team[each.key].name
  policy_arn = aws_iam_policy.team_ssm_access[0].arn
}

output "team_ssm_access" {
  value = {
    enabled              = var.team_ssm_access_enabled
    required_project     = "seonjalal"
    allowed_environments = ["develop", "staging"]
    required_access_tag  = { TeamSSMAccess = "true" }
    session_document     = "SSM-SessionManagerRunShell"
    access_level         = "interactive shell; not read-only OS access"
  }
}
