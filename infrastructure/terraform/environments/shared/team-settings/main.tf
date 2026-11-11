locals {
  teams = {
    fullstack = ["fs-neo", "fs-theo", "fs-ava"]
    ai        = ["ai-dylan", "ai-emet"]
  }
  # fs-theo는 기존 사용자를 삭제하고 같은 이름으로 재생성하여 Terraform이 관리(전원 동일한 방식).
  new_users = toset(["fs-neo", "fs-theo", "fs-ava", "ai-dylan", "ai-emet"])
  stores = {
    for pair in setproduct(["develop", "staging"], keys(local.teams)) :
    "${pair[0]}/${pair[1]}" => { environment = pair[0], team = pair[1] }
  }
  secret_resources = {
    for team in keys(local.teams) : team => [for env in ["develop", "staging"] :
    "arn:aws:secretsmanager:ap-northeast-2:721744297924:secret:/seonjalal/${env}/teams/${team}-??????"]
  }
  parameter_resources = {
    for team in keys(local.teams) : team => [for env in ["develop", "staging"] :
    "arn:aws:ssm:ap-northeast-2:721744297924:parameter/seonjalal/${env}/config/${team}/*"]
  }
  bootstrap_actions = [
    "iam:GetUser", "iam:ChangePassword", "iam:GetAccountPasswordPolicy",
    "iam:CreateVirtualMFADevice", "iam:EnableMFADevice", "iam:ResyncMFADevice",
    "iam:ListMFADevices", "iam:ListVirtualMFADevices", "sts:GetSessionToken", "sts:GetCallerIdentity",
  ]
}

resource "aws_iam_user" "member" {
  for_each      = local.new_users
  name          = each.value
  force_destroy = false
  tags          = { Purpose = "nonproduction-team-settings" }
  lifecycle { prevent_destroy = true }
}

resource "aws_iam_group" "team" {
  for_each = local.teams
  name     = "seonjalal-${each.key}-nonprod-settings"
}

resource "aws_iam_group_membership" "team" {
  for_each   = local.teams
  name       = "seonjalal-${each.key}-settings-members"
  group      = aws_iam_group.team[each.key].name
  users      = each.value
  depends_on = [aws_iam_user.member]
}

# 빈 Secret의 메타데이터만 관리. 비밀값/초기 로그인 비밀번호를 State에 저장하지 않음.
resource "aws_secretsmanager_secret" "team" {
  for_each                = local.stores
  name                    = "/seonjalal/${each.value.environment}/teams/${each.value.team}"
  description             = "Team-owned nonproduction application settings; existing platform DB/JWT secrets excluded"
  recovery_window_in_days = 7
  tags                    = { Environment = each.value.environment, OwnerTeam = each.value.team }
  lifecycle { prevent_destroy = true }
}

resource "aws_iam_group_policy" "settings" {
  for_each = local.teams
  name     = "TeamOwnedNonProductionSettings"
  group    = aws_iam_group.team[each.key].name
  # 넓은 Allow만 추가하면 기존 explicit Deny가 남아 접근이 계속 막히므로
  # 승인된 admin 모드에서는 이 정책 전체를 교체. MFA 자기관리 정책은 그대로 유지.
  policy = var.team_settings_admin_enabled ? file("${path.module}/settings-admin-policy.json") : jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid = "ReadAndUpdateOwnTeamSecretValues", Effect = "Allow",
        Action = ["secretsmanager:DescribeSecret", "secretsmanager:ListSecretVersionIds",
        "secretsmanager:GetSecretValue", "secretsmanager:PutSecretValue"],
        Resource  = local.secret_resources[each.key],
        Condition = { Bool = { "aws:MultiFactorAuthPresent" = "true" } }
      },
      {
        Sid       = "ReadAndWriteOwnTeamConfiguration", Effect = "Allow",
        Action    = ["ssm:GetParameter", "ssm:GetParameters", "ssm:GetParameterHistory", "ssm:GetParametersByPath", "ssm:PutParameter"],
        Resource  = local.parameter_resources[each.key],
        Condition = { Bool = { "aws:MultiFactorAuthPresent" = "true" } }
      },
      # 다른 정책이 남아 있어도 설정 API가 운영/타팀/플랫폼/배포 경로로 확장되지 않도록 제한.
      {
        Sid         = "DenyOtherSecrets", Effect = "Deny", Action = ["secretsmanager:*"],
        NotResource = local.secret_resources[each.key]
      },
      {
        Sid         = "DenyOtherParameters", Effect = "Deny", Action = ["ssm:*"],
        NotResource = local.parameter_resources[each.key]
      },
      {
        Sid = "DenySecretAdministration", Effect = "Deny",
        Action = ["secretsmanager:CreateSecret", "secretsmanager:DeleteSecret", "secretsmanager:UpdateSecret",
          "secretsmanager:PutResourcePolicy", "secretsmanager:DeleteResourcePolicy", "secretsmanager:TagResource",
          "secretsmanager:UntagResource", "secretsmanager:RotateSecret", "secretsmanager:UpdateSecretVersionStage",
        "ssm:DeleteParameter", "ssm:DeleteParameters", "ssm:AddTagsToResource", "ssm:RemoveTagsFromResource"],
        Resource = "*"
      }
    ]
  })
}

resource "aws_iam_group_policy" "self_service" {
  for_each = local.teams
  name     = "OwnPasswordAndMfaOnly"
  group    = aws_iam_group.team[each.key].name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Sid = "OwnPasswordAndMfa", Effect = "Allow",
        Action = ["iam:GetUser", "iam:ChangePassword", "iam:EnableMFADevice",
        "iam:DeactivateMFADevice", "iam:ListMFADevices", "iam:ResyncMFADevice"],
        Resource = "arn:aws:iam::721744297924:user/$${aws:username}"
      },
      { Sid      = "CreateOwnMfa", Effect = "Allow", Action = ["iam:CreateVirtualMFADevice"],
        Resource = "arn:aws:iam::721744297924:mfa/$${aws:username}"
      },
      { Sid    = "PasswordPolicyAndMfaBootstrap", Effect = "Allow",
        Action = ["iam:GetAccountPasswordPolicy", "iam:ListVirtualMFADevices", "sts:GetSessionToken", "sts:GetCallerIdentity"], Resource = "*"
      },
      { Sid      = "RequireMfaForNonBootstrapActions", Effect = "Deny", NotAction = local.bootstrap_actions,
        Resource = "*", Condition = { BoolIfExists = { "aws:MultiFactorAuthPresent" = "false" } }
      }
    ]
  })
}
