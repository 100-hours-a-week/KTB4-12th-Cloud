variable "production_log_read_enabled" {
  type        = bool
  default     = false
  description = "MFA 인증 팀원에게 운영 ECS CloudWatch Logs의 읽기·조회(Logs Insights)만 허용. 서버 셸·쓰기·삭제 권한 없음."
}

locals {
  production_account_id = "721744297924"

  # 운영 Terraform이 만드는 ECS 로그 그룹. 와일드카드 없이 정확한 이름만 허용.
  # - be_split_runtime: /ecs/seonjalal-v2-production-{main-backend,payment-server}
  # - ai_ecs_runtime:   /ecs/seonjalal-v2-production-ai
  # - runtime(휴면 구 Backend, 전환 이전 로그 보존): /ecs/seonjalal-v2-production/backend
  # - RDS: AI PostgreSQL(ai-database)과 MySQL 에러 로그(foundation). RDS가 /aws/rds/instance/<식별자>/<로그종류>로 생성.
  #   MySQL 로그 그룹은 foundation Root에서 내보내기를 켠 뒤에 생기며, 그 전에는 허용만 존재하고 대상은 없음.
  production_log_group_names = [
    "/ecs/seonjalal-v2-production-main-backend",
    "/ecs/seonjalal-v2-production-payment-server",
    "/ecs/seonjalal-v2-production-ai",
    "/ecs/seonjalal-v2-production/backend",
    "/aws/rds/instance/seonjalal-v2-production-ai-postgres/postgresql",
    "/aws/rds/instance/seonjalal-v2-production/error",
  ]
  production_log_group_arns = [
    for name in local.production_log_group_names :
    "arn:aws:logs:ap-northeast-2:${local.production_account_id}:log-group:${name}"
  ]
  # 이벤트 조회 API는 로그 그룹 ARN과 스트림 ARN(`:*`)을 모두 요구할 수 있음.
  production_log_resources = flatten([
    for arn in local.production_log_group_arns : [arn, "${arn}:*"]
  ])
}

# 팀별 설정 정책과 분리한 공통 관리형 정책. 기존 Secret/SSM 권한은 변경하지 않음.
resource "aws_iam_policy" "production_log_read" {
  count       = var.production_log_read_enabled ? 1 : 0
  name        = "SeonjalalTeamProductionLogRead"
  description = "MFA read-only access to production ECS application and RDS log groups; no server access"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # 지정한 운영 로그 그룹의 이벤트 조회 및 Logs Insights 쿼리 시작.
        Sid    = "ReadAndQueryProductionApplicationLogs"
        Effect = "Allow"
        Action = [
          "logs:StartQuery",
          "logs:GetLogEvents",
          "logs:FilterLogEvents",
          "logs:DescribeLogStreams",
          "logs:GetLogGroupFields",
          "logs:GetLogRecord",
        ]
        Resource = local.production_log_resources
        Condition = {
          Bool         = { "aws:MultiFactorAuthPresent" = "true" }
          StringEquals = { "aws:RequestedRegion" = "ap-northeast-2" }
        }
      },
      {
        # 이 API들은 리소스 단위 권한 지정을 지원하지 않아 "*" 필요.
        # 쿼리 결과 조회·중지와 콘솔의 로그 그룹 목록(이름·보존기간 메타데이터)만 해당.
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
        # 위 Allow가 어떤 이유로 넓어져도 운영 로그의 변경·삭제·구독 경로는 열리지 않도록 명시 차단.
        Sid    = "DenyProductionLogMutation"
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
        Resource = local.production_log_resources
      },
    ]
  })
}

resource "aws_iam_group_policy_attachment" "production_log_read" {
  for_each   = var.production_log_read_enabled ? local.teams : {}
  group      = aws_iam_group.team[each.key].name
  policy_arn = aws_iam_policy.production_log_read[0].arn
}

output "production_log_read" {
  value = {
    enabled          = var.production_log_read_enabled
    log_groups       = local.production_log_group_names
    mfa_required     = true
    access_level     = "read-only CloudWatch Logs and Logs Insights; no shell, no Secret values"
    retention_days   = 30
    sensitive_notice = "로그에 토큰·개인정보가 섞일 수 있으므로 원문 전체 복사·스크린샷 공유 금지"
  }
}
