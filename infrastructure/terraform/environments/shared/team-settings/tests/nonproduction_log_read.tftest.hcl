# 실제 AWS 호출 없이 스테이징 로그 읽기 정책의 opt-in·범위·MFA 조건 검사.
mock_provider "aws" {}

run "disabled_by_default" {
  command = plan
  assert {
    condition     = length(aws_iam_policy.nonproduction_log_read) == 0 && length(aws_iam_group_policy_attachment.nonproduction_log_read) == 0
    error_message = "명시 활성화 전 스테이징 로그 정책 생성/연결 금지."
  }
}

run "enabled_for_both_existing_teams_only" {
  command = plan
  variables {
    nonproduction_log_read_enabled = true
  }
  assert {
    condition     = length(aws_iam_policy.nonproduction_log_read) == 1 && toset(keys(aws_iam_group_policy_attachment.nonproduction_log_read)) == toset(["fullstack", "ai"])
    error_message = "공통 관리형 정책 하나를 기존 두 그룹에만 연결 필요."
  }
  assert {
    condition = alltrue([for statement in jsondecode(aws_iam_policy.nonproduction_log_read[0].policy).Statement :
      statement.Effect != "Allow" || try(statement.Condition.Bool["aws:MultiFactorAuthPresent"], "false") == "true"
    ])
    error_message = "모든 Allow에 MFA 조건 필요."
  }
  assert {
    # 운영 로그 그룹이 스테이징 정책에 섞이면 안 됨.
    condition = alltrue([for resource in jsondecode(aws_iam_policy.nonproduction_log_read[0].policy).Statement[0].Resource :
      !strcontains(resource, "production") && !strcontains(replace(resource, ":*", ""), "*")
    ])
    error_message = "스테이징 정책에 운영 로그 그룹·와일드카드 포함 금지."
  }
  assert {
    condition = toset(jsondecode(aws_iam_policy.nonproduction_log_read[0].policy).Statement[1].Action) == toset([
      "logs:DescribeLogGroups", "logs:GetQueryResults", "logs:StopQuery", "logs:DescribeQueries"
    ])
    error_message = "Resource * Allow는 목록·쿼리 결과 API 4개로 한정."
  }
  assert {
    condition     = jsondecode(aws_iam_policy.nonproduction_log_read[0].policy).Statement[2].Effect == "Deny"
    error_message = "스테이징 로그 변경·삭제 명시 Deny 유지 필요."
  }
}
