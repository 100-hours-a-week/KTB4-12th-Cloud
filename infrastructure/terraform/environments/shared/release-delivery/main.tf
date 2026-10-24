variable "deployment_targets" {
  description = "실제Service/CloudFront/TaskRole준비후에만환경별CD권한생성. 기본은발행권한만준비."
  type = map(object({
    cloudfront_distribution_id = string
    task_role_arns             = set(string)
    oidc_subject               = string
  }))
  default = {}
  validation {
    condition = alltrue([for env, target in var.deployment_targets :
      contains(["staging", "production"], env) && can(regex("^[A-Z0-9]{8,32}$", target.cloudfront_distribution_id)) &&
      contains(["repo:100-hours-a-week/KTB4-12th-Cloud:environment:v2-${env}",
        "repo:100-hours-a-week@167328634/KTB4-12th-Cloud@1344623960:environment:v2-${env}"
      ], target.oidc_subject) &&
      length(target.task_role_arns) > 0 && alltrue([for arn in target.task_role_arns :
        can(regex("^arn:aws:iam::721744297924:role/seonjalal-v2-${env}-[A-Za-z0-9+=,.@_/-]+$", arn))
      ])
    ])
    error_message = "검토한비운영/운영환경별실제Distribution및정확한해당환경Task/ExecutionRoleARN필요."
  }
}

locals {
  account = "721744297924"
  region  = "ap-northeast-2"
  bucket  = "seonjalal-v2-frontend-${local.account}-${local.region}"
  publishers = {
    backend  = { repo = "KTB4-12th-BE", repo_id = "1344623893" }
    frontend = { repo = "KTB4-12th-FE", repo_id = "1344623849" }
  }
  repository_arns = { backend = data.aws_ecr_repository.backend.arn, frontend = aws_ecr_repository.frontend.arn }
  # 배포 대상 서비스 이름. 블루-그린 두 번째 슬롯(-b)을 포함한다. CD는 서비스 중인 슬롯만 선택하므로 둘 다 갱신 가능해야 한다.
  deploy_service_names = ["backend", "main-backend", "payment-server", "main-backend-b", "payment-server-b"]
}

data "aws_iam_openid_connect_provider" "github" { url = "https://token.actions.githubusercontent.com" }
data "aws_ecr_repository" "backend" { name = "seonjalal/production/backend" }

resource "aws_ecr_repository" "frontend" {
  name                 = "seonjalal/production/frontend"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = false
  image_scanning_configuration { scan_on_push = true }
  lifecycle { prevent_destroy = true }
}

# 현재조직의owner/repo ID포함OIDC sub형식유지. main만발행,배포권한없음.
resource "aws_iam_role" "publisher" {
  for_each = local.publishers
  name     = "seonjalal-v2-release-${each.key}-publisher"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect    = "Allow", Action = "sts:AssumeRoleWithWebIdentity",
    Principal = { Federated = data.aws_iam_openid_connect_provider.github.arn },
    Condition = { StringEquals = {
      "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com",
      "token.actions.githubusercontent.com:sub" = "repo:100-hours-a-week@167328634/${each.value.repo}@${each.value.repo_id}:ref:refs/heads/main"
    } }
  }] })
}

resource "aws_iam_role_policy" "publisher" {
  for_each = local.publishers
  role     = aws_iam_role.publisher[each.key].id
  name     = "PublishOwnImmutableReleaseRepository"
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = "*" },
    { Effect = "Allow", Action = ["ecr:DescribeRepositories", "ecr:DescribeImages", "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability", "ecr:InitiateLayerUpload",
    "ecr:UploadLayerPart", "ecr:CompleteLayerUpload", "ecr:PutImage"], Resource = local.repository_arns[each.key] }
  ] })
}

# 배포 Role은 Cloud Repository의 환경 Identity만 허용. GitHub Environment의 branch도 main으로 제한.
resource "aws_iam_role" "deployer" {
  for_each = var.deployment_targets
  name     = "seonjalal-v2-${each.key}-release-deployer"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{
    Effect    = "Allow", Action = "sts:AssumeRoleWithWebIdentity",
    Principal = { Federated = data.aws_iam_openid_connect_provider.github.arn },
    Condition = { StringEquals = {
      "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com",
      "token.actions.githubusercontent.com:sub" = each.value.oidc_subject
    } }
  }] })
}

resource "aws_iam_role_policy" "deployer" {
  for_each = var.deployment_targets
  role     = aws_iam_role.deployer[each.key].id
  name     = "DeliverOnlySelectedEnvironment"
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = "*" },
    { Effect = "Allow", Action = ["ecr:DescribeRepositories", "ecr:DescribeImages", "ecr:BatchGetImage",
    "ecr:GetDownloadUrlForLayer", "ecr:BatchCheckLayerAvailability"], Resource = values(local.repository_arns) },
    { Effect = "Allow", Action = ["ecs:DescribeServices", "ecs:UpdateService"], Resource = [
      for name in local.deploy_service_names :
      "arn:aws:ecs:${local.region}:${local.account}:service/seonjalal-v2-${each.key}-cluster/seonjalal-v2-${each.key}-${name}"
    ] },
    { Effect = "Allow", Action = ["ecs:RegisterTaskDefinition"], Resource = [
      for name in local.deploy_service_names :
      "arn:aws:ecs:${local.region}:${local.account}:task-definition/seonjalal-v2-${each.key}-${name}:*"
    ] },
    # 이 조회 API는 리소스 수준 권한을 지원하지 않음. 실행/등록/PassRole은 해당 환경만 허용.
    { Effect = "Allow", Action = ["ecs:DescribeTaskDefinition"], Resource = "*" },
    { Effect = "Allow", Action = ["iam:PassRole"], Resource = sort(tolist(each.value.task_role_arns)),
    Condition = { StringEquals = { "iam:PassedToService" = "ecs-tasks.amazonaws.com" } } },
    { Effect = "Allow", Action = ["s3:GetBucketPublicAccessBlock", "s3:GetBucketVersioning", "s3:GetBucketPolicy"],
    Resource = "arn:aws:s3:::${local.bucket}" },
    { Effect = "Allow", Action = ["s3:ListBucket"], Resource = "arn:aws:s3:::${local.bucket}",
    Condition = { StringLike = { "s3:prefix" = ["frontend/${each.key}/", "frontend/${each.key}/*"] } } },
    { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject"],
    Resource = "arn:aws:s3:::${local.bucket}/frontend/${each.key}/*" },
    { Effect = "Allow", Action = ["cloudfront:GetDistribution", "cloudfront:CreateInvalidation", "cloudfront:GetInvalidation"],
    Resource = "arn:aws:cloudfront::${local.account}:distribution/${each.value.cloudfront_distribution_id}" }
  ] })
}

output "release_delivery" {
  value = {
    publisher_roles                    = { for name, role in aws_iam_role.publisher : name => role.arn }
    deployer_roles                     = { for name, role in aws_iam_role.deployer : name => role.arn }
    frontend_ecr                       = aws_ecr_repository.frontend.repository_url
    backend_ecr                        = data.aws_ecr_repository.backend.repository_url
    readiness_requires_live_validation = true
  }
}
