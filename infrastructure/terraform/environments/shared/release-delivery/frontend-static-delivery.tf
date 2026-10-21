variable "frontend_static_targets" {
  description = "실제 CloudFront Distribution ID를 확인한 환경에만 FE 정적 배포 Role 생성. 기본값은 생성 없음."
  type        = map(string)
  default     = {}

  validation {
    condition = alltrue([
      for environment, distribution_id in var.frontend_static_targets :
      contains(["staging", "production"], environment) && can(regex("^E[A-Z0-9]{7,31}$", distribution_id))
    ]) && length(distinct(values(var.frontend_static_targets))) == length(var.frontend_static_targets)
    error_message = "staging/production의 실제 CloudFront Distribution ID를 서로 다르게 입력해야 합니다."
  }
}

# FE GitHub Environment별 OIDC Identity만 허용. main Branch 제한은 FE Workflow와
# GitHub Environment 설정에서 확인하고, IAM에서는 정확한 Repository/Environment를 고정한다.
resource "aws_iam_role" "frontend_static_deployer" {
  for_each = var.frontend_static_targets
  name     = "seonjalal-v2-${each.key}-frontend-deployer"

  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect = "Allow"
    Action = "sts:AssumeRoleWithWebIdentity"
    Principal = {
      Federated = data.aws_iam_openid_connect_provider.github.arn
    }
    Condition = { StringEquals = {
      "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
      "token.actions.githubusercontent.com:sub" = [
        "repo:100-hours-a-week/KTB4-12th-FE:environment:fe-v2-${each.key}",
        "repo:100-hours-a-week@167328634/KTB4-12th-FE@1344623849:environment:fe-v2-${each.key}"
      ]
    } }
  }] })
}

# 릴리스 Artifact는 읽기 전용, 대상 환경 Prefix만 쓰기 허용. CloudFront도 지정된
# Distribution의 조회·Invalidation에 한정하며 ECS/SSM/다른 환경 권한은 부여하지 않는다.
resource "aws_iam_role_policy" "frontend_static_deployer" {
  for_each = var.frontend_static_targets
  role     = aws_iam_role.frontend_static_deployer[each.key].id
  name     = "DeliverOnlyFrontendStatic${title(each.key)}"

  policy = jsonencode({ Version = "2012-10-17", Statement = [
    {
      Effect   = "Allow"
      Action   = ["s3:GetBucketPublicAccessBlock", "s3:GetBucketVersioning", "s3:GetBucketPolicy"]
      Resource = "arn:aws:s3:::${local.bucket}"
    },
    {
      Effect   = "Allow"
      Action   = ["s3:ListBucket"]
      Resource = "arn:aws:s3:::${local.bucket}"
      Condition = { StringLike = {
        "s3:prefix" = ["releases/frontend/*", "frontend/${each.key}/", "frontend/${each.key}/*"]
      } }
    },
    {
      Effect   = "Allow"
      Action   = ["s3:GetObject"]
      Resource = ["arn:aws:s3:::${local.bucket}/releases/frontend/*", "arn:aws:s3:::${local.bucket}/frontend/${each.key}/*"]
    },
    {
      Effect   = "Allow"
      Action   = ["s3:PutObject"]
      Resource = "arn:aws:s3:::${local.bucket}/frontend/${each.key}/*"
    },
    {
      Effect   = "Allow"
      Action   = ["cloudfront:GetDistribution", "cloudfront:CreateInvalidation", "cloudfront:GetInvalidation"]
      Resource = "arn:aws:cloudfront::${local.account}:distribution/${each.value}"
    }
  ] })
}

output "frontend_static_deployer_roles" {
  value = { for environment, role in aws_iam_role.frontend_static_deployer : environment => role.arn }
}
