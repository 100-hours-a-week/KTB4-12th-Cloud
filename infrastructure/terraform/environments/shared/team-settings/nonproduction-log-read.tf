variable "nonproduction_log_read_enabled" {
  type        = bool
  default     = false
  description = "MFA 인증 팀원에게 스테이징 ECS·RDS CloudWatch Logs의 읽기·조회(Logs Insights)만 허용. 서버 셸·쓰기·삭제 권한 없음."
}

locals {
  # 스테이징 로그 그룹. 와일드카드 없이 정확한 이름만 허용.
  # - staging/rehearsal: /ecs/seonjalal-v2-staging/backend (name_prefix 기반)
  # - be_split_runtime·ai_ecs_runtime(environment=staging): /ecs/seonjalal-v2-staging-{main-backend,payment-server,ai}
  # - staging/ai-database: PostgreSQL 로그
  # 개발(develop)은 EC2 Docker Compose 직접 실행이라 CloudWatch 로그 그룹이 없어 이 정책 대상이 아님(SSM 셸 `docker logs` 사용).
  staging_log_group_names = [
    "/ecs/seonjalal-v2-staging/backend",
    "/ecs/seonjalal-v2-staging-main-backend",
    "/ecs/seonjalal-v2-staging-payment-server",
    "/ecs/seonjalal-v2-staging-ai",
    "/aws/rds/instance/seonjalal-v2-staging-ai-postgres/postgresql",
  ]
  staging_log_resources = flatten([
    for name in local.staging_log_group_names : [
      "arn:aws:logs:ap-northeast-2:${local.production_account_id}:log-group:${name}",
      "arn:aws:logs:ap-northeast-2:${local.production_account_id}:log-group:${name}:*",
    ]
  ])
}

# 운영 정책과 분리해 비운영·운영 권한을 나중에 따로 조정할 수 있게 함.
resource "aws_iam_policy" "nonproduction_log_read" {
  count       = var.nonproduction_log_read_enabled ? 1 : 0
  name        = "SeonjalalTeamStagingLogRead"
  description = "MFA read-only access to staging ECS and RDS log groups; no server access"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ReadAndQueryStagingLogs"
        Effect = "Allow"
        Action = [
          "logs:StartQuery",
          "logs:GetLogEvents",
          "logs:FilterLogEvents",
          "logs:DescribeLogStreams",
          "logs:GetLogGroupFields",
          "logs:GetLogRecord",
        ]
        Resource = local.staging_log_resources
        Condition = {
          Bool         = { "aws:MultiFactorAuthPresent" = "true" }
          StringEquals = { "aws:RequestedRegion" = "ap-northeast-2" }
        }
      },
      {
        # 리소스 지정이 불가능한 목록·쿼리 결과 API. 운영 정책과 같은 문장이며 함께 붙어도 권한이 늘지 않음.
        Sid    = "ListLogGroupsAndManageOwnQueries"
        Effect = "Allow"
        Action = [
          "logs:DescribeLogGroups",
          "logs:GetQueryResults",
          "logs:StopQuery",
          "logs:DescribeQueries",
        ]
        Resource = "*"
        Condition = {
          Bool         = { "aws:MultiFactorAuthPresent" = "true" }
          StringEquals = { "aws:RequestedRegion" = "ap-northeast-2" }
        }
      },
      {
        Sid    = "DenyStagingLogMutation"
        Effect = "Deny"
        Action = [
          "logs:DeleteLogGroup",
          "logs:DeleteLogStream",
          "logs:PutRetentionPolicy",
          "logs:DeleteRetentionPolicy",
          "logs:PutSubscriptionFilter",
          "logs:PutResourcePolicy",
          "logs:AssociateKmsKey",
          "logs:DisassociateKmsKey",
          "logs:PutLogEvents",
          "logs:PutDataProtectionPolicy",
          "logs:DeleteDataProtectionPolicy",
          "logs:CreateExportTask",
        ]
        Resource = local.staging_log_resources
      },
    ]
  })
}

resource "aws_iam_group_policy_attachment" "nonproduction_log_read" {
  for_each   = var.nonproduction_log_read_enabled ? local.teams : {}
  group      = aws_iam_group.team[each.key].name
  policy_arn = aws_iam_policy.nonproduction_log_read[0].arn
}

output "nonproduction_log_read" {
  value = {
    enabled          = var.nonproduction_log_read_enabled
    log_groups       = local.staging_log_group_names
    mfa_required     = true
    access_level     = "read-only CloudWatch Logs and Logs Insights; no shell, no Secret values"
    develop_note     = "개발은 CloudWatch 로그 그룹 없음. SSM 셸 docker logs 사용"
    sensitive_notice = "로그에 토큰·개인정보가 섞일 수 있으므로 원문 전체 복사·스크린샷 공유 금지"
  }
}
