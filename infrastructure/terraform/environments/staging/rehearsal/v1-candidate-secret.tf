variable "v1_candidate_secret_role_name" {
  type        = string
  default     = null
  description = "사용자 조회로 확인한 벤치마크 Role. null이면 Secret 접근 정책 생성 없음."
  validation {
    condition = var.v1_candidate_secret_role_name == null || (
      var.v1_candidate_secret_role_name == "seonjalal-v1-develop-role" &&
      var.v1_cutover_network != null && var.database_fixture_verified && !var.database_cutover_verified
    )
    error_message = "확인된 벤치마크 Role과 격리 Fixture 경로에서만 후보 Secret 읽기 허용. 운영 Role 지정 금지."
  }
}

# 기존 Role/정책은 인수하거나 수정하지 않고 이 시험용 정책 하나만 관리.
# Secret JSON 내부의 특정 키만 IAM으로 제한할 수 없으므로 전체 시험 Secret 읽기 권한.
resource "aws_iam_role_policy" "v1_candidate_runtime" {
  count = var.v1_candidate_secret_role_name == null ? 0 : 1
  name  = "SeonjalalStagingFixtureRuntimeRead"
  role  = var.v1_candidate_secret_role_name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid      = "ReadCurrentStagingFixtureRuntimeOnly"
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = aws_secretsmanager_secret.backend_runtime.arn
      Condition = {
        StringEquals = { "secretsmanager:VersionStage" = "AWSCURRENT" }
      }
    }]
  })
  # Source/운영 서브넷 분리 등의 기존 검사에 실패하면 권한 추가도 중단.
  depends_on = [aws_route_table.v1_cutover]
}
