# 앱 발행 권한과 환경 배포 권한 분리. 기본은 BE ECR·FE S3 발행만 준비.
variable "deployment_targets" {
  description = "Cloud BE 전용 배포 대상. 실제 Task/Execution Role과 확인한 Cloud OIDC sub만 입력."
  type        = map(object({ task_role_arns = set(string), oidc_subject = string }))
  default     = {}
  validation {
    condition = alltrue([for env, target in var.deployment_targets :
      contains(["staging", "production"], env) && length(target.task_role_arns) > 0 &&
      contains(["repo:100-hours-a-week/KTB4-12th-Cloud:environment:v2-${env}",
      "repo:100-hours-a-week@167328634/KTB4-12th-Cloud@1344623960:environment:v2-${env}"], target.oidc_subject) &&
      alltrue([for arn in target.task_role_arns : can(regex("^arn:aws:iam::721744297924:role/seonjalal-v2-${env}-[A-Za-z0-9+=,.@_/-]+$", arn))])
    ])
    error_message = "해당 환경 Role과 정확한 Cloud OIDC sub 필요."
  }
}
variable "frontend_deployment_targets" {
  description = "FE 레포 소유 배포 대상. 실제 Distribution 준비 후 입력."
  type        = map(object({ cloudfront_distribution_id = string }))
  default     = {}
  validation {
    condition = alltrue([for env, target in var.frontend_deployment_targets :
      contains(["staging", "production"], env) && can(regex("^[A-Z0-9]{8,32}$", target.cloudfront_distribution_id))
    ])
    error_message = "유효한 FE 환경과 실제 Distribution ID 필요."
  }
}
locals {
  account          = "721744297924"
  region           = "ap-northeast-2"
  bucket           = "seonjalal-v2-frontend-${local.account}-${local.region}"
  frontend_subject = "repo:100-hours-a-week@167328634/KTB4-12th-FE@1344623849"
}
data "aws_iam_openid_connect_provider" "github" { url = "https://token.actions.githubusercontent.com" }
data "aws_ecr_repository" "backend" { name = "seonjalal/production/backend" }

resource "aws_iam_role" "publisher" {
  for_each = { backend = "repo:100-hours-a-week@167328634/KTB4-12th-BE@1344623893", frontend = local.frontend_subject }
  name     = "seonjalal-v2-release-${each.key}-publisher"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect    = "Allow", Action = "sts:AssumeRoleWithWebIdentity",
    Principal = { Federated = data.aws_iam_openid_connect_provider.github.arn },
    Condition = { StringEquals = {
      "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com",
      "token.actions.githubusercontent.com:sub" = "${each.value}:ref:refs/heads/main"
    } }
  }] })
}
resource "aws_iam_role_policy" "backend_publisher" {
  role = aws_iam_role.publisher["backend"].id
  name = "PublishOwnImmutableReleaseRepository"
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = "*" },
    { Effect = "Allow", Action = ["ecr:DescribeRepositories", "ecr:DescribeImages", "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability", "ecr:InitiateLayerUpload",
    "ecr:UploadLayerPart", "ecr:CompleteLayerUpload", "ecr:PutImage"], Resource = data.aws_ecr_repository.backend.arn }
  ] })
}
resource "aws_iam_role_policy" "frontend_publisher" {
  role = aws_iam_role.publisher["frontend"].id
  name = "PublishFrontendStaticReleasesOnly"
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["s3:GetBucketPublicAccessBlock", "s3:GetBucketVersioning"], Resource = "arn:aws:s3:::${local.bucket}" },
    { Effect = "Allow", Action = ["s3:ListBucket"], Resource = "arn:aws:s3:::${local.bucket}",
    Condition = { StringLike = { "s3:prefix" = ["releases/frontend/*"] } } },
    { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject"], Resource = "arn:aws:s3:::${local.bucket}/releases/frontend/*" }
  ] })
}
resource "aws_iam_role" "deployer" {
  for_each = var.deployment_targets
  name     = "seonjalal-v2-${each.key}-release-deployer"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect    = "Allow", Action = "sts:AssumeRoleWithWebIdentity",
    Principal = { Federated = data.aws_iam_openid_connect_provider.github.arn },
    Condition = { StringEquals = { "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com",
    "token.actions.githubusercontent.com:sub" = each.value.oidc_subject } }
  }] })
}
resource "aws_iam_role_policy" "deployer" {
  for_each = var.deployment_targets
  role     = aws_iam_role.deployer[each.key].id
  name     = "DeliverBackendOnlySelectedEnvironment"
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = "*" },
    { Effect = "Allow", Action = ["ecr:DescribeRepositories", "ecr:DescribeImages", "ecr:BatchGetImage",
    "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"], Resource = data.aws_ecr_repository.backend.arn },
    { Effect = "Allow", Action = ["ecs:DescribeServices", "ecs:UpdateService"], Resource = [
      for name in ["backend", "main-backend", "payment-server"] :
    "arn:aws:ecs:${local.region}:${local.account}:service/seonjalal-v2-${each.key}-cluster/seonjalal-v2-${each.key}-${name}"] },
    { Effect = "Allow", Action = ["ecs:RegisterTaskDefinition"], Resource = [
      for name in ["backend", "main-backend", "payment-server"] :
    "arn:aws:ecs:${local.region}:${local.account}:task-definition/seonjalal-v2-${each.key}-${name}:*"] },
    # AWS의 리소스 수준 권한 미지원 조회 API.
    { Effect = "Allow", Action = ["ecs:DescribeTaskDefinition"], Resource = "*" },
    { Effect = "Allow", Action = ["iam:PassRole"], Resource = sort(tolist(each.value.task_role_arns)),
    Condition = { StringEquals = { "iam:PassedToService" = "ecs-tasks.amazonaws.com" } } }
  ] })
}
resource "aws_iam_role" "frontend_deployer" {
  for_each = var.frontend_deployment_targets
  name     = "seonjalal-v2-${each.key}-frontend-deployer"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect    = "Allow", Action = "sts:AssumeRoleWithWebIdentity",
    Principal = { Federated = data.aws_iam_openid_connect_provider.github.arn },
    Condition = { StringEquals = { "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com",
    "token.actions.githubusercontent.com:sub" = "${local.frontend_subject}:environment:fe-v2-${each.key}" } }
  }] })
}
resource "aws_iam_role_policy" "frontend_deployer" {
  for_each = var.frontend_deployment_targets
  role     = aws_iam_role.frontend_deployer[each.key].id
  name     = "DeliverFrontendOnlySelectedEnvironment"
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["s3:GetBucketPublicAccessBlock", "s3:GetBucketVersioning", "s3:GetBucketPolicy"],
    Resource = "arn:aws:s3:::${local.bucket}" },
    { Effect = "Allow", Action = ["s3:ListBucket"], Resource = "arn:aws:s3:::${local.bucket}",
    Condition = { StringLike = { "s3:prefix" = ["releases/frontend/*", "frontend/${each.key}/*"] } } },
    { Effect = "Allow", Action = ["s3:GetObject"], Resource = [
    "arn:aws:s3:::${local.bucket}/releases/frontend/*", "arn:aws:s3:::${local.bucket}/frontend/${each.key}/*"] },
    { Effect = "Allow", Action = ["s3:PutObject"], Resource = "arn:aws:s3:::${local.bucket}/frontend/${each.key}/*" },
    { Effect = "Allow", Action = ["cloudfront:GetDistribution", "cloudfront:CreateInvalidation", "cloudfront:GetInvalidation"],
    Resource = "arn:aws:cloudfront::${local.account}:distribution/${each.value.cloudfront_distribution_id}" }
  ] })
}
output "release_delivery" {
  value = {
    publisher_roles                    = { for name, role in aws_iam_role.publisher : name => role.arn }
    deployer_roles                     = { for name, role in aws_iam_role.deployer : name => role.arn }
    frontend_deployer_roles            = { for name, role in aws_iam_role.frontend_deployer : name => role.arn }
    backend_ecr                        = data.aws_ecr_repository.backend.repository_url
    frontend_release_prefix            = "s3://${local.bucket}/releases/frontend/"
    readiness_requires_live_validation = true
  }
}
