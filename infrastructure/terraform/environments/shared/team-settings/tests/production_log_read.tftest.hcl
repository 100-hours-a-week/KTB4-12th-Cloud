# 실제 AWS 호출 없이 운영 로그 읽기 정책의 opt-in·범위·MFA 조건 검사.
mock_provider "aws" {}

run "disabled_by_default" {
  command = plan
  assert {
    condition     = length(aws_iam_policy.production_log_read) == 0 && length(aws_iam_group_policy_attachment.production_log_read) == 0
    error_message = "명시 활성화 전 운영 로그 정책 생성/연결 금지."
  }
}

run "enabled_for_both_existing_teams_only" {
  command = plan
  variables {
    production_log_read_enabled = true
  }
  assert {
    condition     = length(aws_iam_policy.production_log_read) == 1 && toset(keys(aws_iam_group_policy_attachment.production_log_read)) == toset(["fullstack", "ai"])
    error_message = "공통 관리형 정책 하나를 기존 두 그룹에만 연결 필요."
  }
  assert {
    condition = alltrue([for statement in jsondecode(aws_iam_policy.production_log_read[0].policy).Statement :
      statement.Effect != "Allow" || try(statement.Condition.Bool["aws:MultiFactorAuthPresent"], "false") == "true"
    ])
    error_message = "모든 Allow에 MFA 조건 필요."
  }
  assert {
    condition = alltrue([for statement in jsondecode(aws_iam_policy.production_log_read[0].policy).Statement :
      statement.Effect != "Allow" || try(statement.Condition.StringEquals["aws:RequestedRegion"], "") == "ap-northeast-2"
    ])
    error_message = "모든 Allow는 서울 리전으로 제한 필요."
  }
  assert {
    # 조회 Allow는 정확한 운영 ECS·RDS 로그 그룹 ARN만 사용. 로그 그룹 와일드카드와 스테이징 그룹 금지.
    condition = alltrue([for resource in jsondecode(aws_iam_policy.production_log_read[0].policy).Statement[0].Resource :
      (startswith(resource, "arn:aws:logs:ap-northeast-2:721744297924:log-group:/ecs/seonjalal-v2-production") ||
      startswith(resource, "arn:aws:logs:ap-northeast-2:721744297924:log-group:/aws/rds/instance/seonjalal-v2-production")) &&
      !strcontains(replace(resource, ":*", ""), "*")
    ])
    error_message = "이벤트 조회 범위는 정확한 운영 ECS·RDS 로그 그룹만 허용."
  }
  assert {
    condition = alltrue([for action in jsondecode(aws_iam_policy.production_log_read[0].policy).Statement[0].Action :
      !contains(["logs:PutLogEvents", "logs:DeleteLogGroup", "logs:CreateLogGroup"], action)
    ])
    error_message = "운영 로그 정책에 쓰기·삭제 동작 포함 금지."
  }
  assert {
    # 리소스 와일드카드 Allow는 리소스 지정이 불가능한 4개 조회 API로 한정.
    condition = toset(jsondecode(aws_iam_policy.production_log_read[0].policy).Statement[1].Action) == toset([
      "logs:DescribeLogGroups", "logs:GetQueryResults", "logs:StopQuery", "logs:DescribeQueries"
    ])
    error_message = "Resource * Allow는 목록·쿼리 결과 API 4개로 한정."
  }
  assert {
    condition     = jsondecode(aws_iam_policy.production_log_read[0].policy).Statement[2].Effect == "Deny"
    error_message = "운영 로그 변경·삭제 명시 Deny 유지 필요."
  }
  assert {
    # 기존 팀 설정·MFA 정책은 그대로 유지.
    condition = alltrue([for policy in values(aws_iam_group_policy.self_service) :
      jsondecode(policy.policy).Statement[3].Effect == "Deny"
    ])
    error_message = "기존 MFA 미인증 차단 보존 필요."
  }
}
