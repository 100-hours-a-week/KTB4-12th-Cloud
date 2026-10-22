# 기존 조직 OIDC Provider를 재사용한다. AI main 이미지 발행과 Cloud ECS 배포는 별도 역할이다.
data "aws_iam_openid_connect_provider" "ai_publisher" {
  url = "https://token.actions.githubusercontent.com"
}

resource "aws_iam_role" "ai_image_publisher" {
  name                 = "seonjalal-v2-production-ai-image-publisher"
  description          = "AI main publishes only four production AI images"
  max_session_duration = 3600

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRoleWithWebIdentity"
      Principal = { Federated = data.aws_iam_openid_connect_provider.ai_publisher.arn }
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          "token.actions.githubusercontent.com:sub" = "repo:100-hours-a-week@167328634/KTB4-12th-AI@1344623933:ref:refs/heads/main"
        }
      }
    }]
  })

  tags = {
    Name               = "seonjalal-v2-production-ai-image-publisher"
    Component          = "github-oidc"
    Workload           = "ai"
    GitHubRepo         = "100-hours-a-week/KTB4-12th-AI"
    Access             = "github-oidc"
    Lifecycle          = "persistent"
    BackupPolicy       = "not-applicable"
    DataClassification = "none"
  }
}

resource "aws_iam_role_policy" "ai_image_publish" {
  name = "PublishOnlyProductionAiImages"
  role = aws_iam_role.ai_image_publisher.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # ECR 로그인 Token API는 Repository 수준 Resource 제한을 지원하지 않는다.
        Sid      = "EcrLogin"
        Effect   = "Allow"
        Action   = ["ecr:GetAuthorizationToken"]
        Resource = "*"
      },
      {
        Sid    = "PublishOnlyFourProductionAiRepositories"
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:BatchGetImage",
          "ecr:CompleteLayerUpload",
          "ecr:DescribeImages",
          "ecr:DescribeRepositories",
          "ecr:GetDownloadUrlForLayer",
          "ecr:InitiateLayerUpload",
          "ecr:PutImage",
          "ecr:UploadLayerPart",
        ]
        Resource = [for workload in sort(tolist(local.ai_ecr_workloads)) : aws_ecr_repository.ai[workload].arn]
      },
    ]
  })
}
