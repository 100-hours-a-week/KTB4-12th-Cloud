# 실제 AWS 호출 없이 AI main OIDC 주체와 운영 네 저장소 최소권한만 검증.
mock_provider "aws" {}

variables {
  aws_account_id = "000000000000"
  aws_profile    = null
}

override_data {
  target = data.aws_iam_openid_connect_provider.ai_publisher
  values = { arn = "arn:aws:iam::000000000000:oidc-provider/token.actions.githubusercontent.com" }
}

override_resource {
  target          = aws_ecr_repository.ai["chat"]
  override_during = plan
  values          = { arn = "arn:aws:ecr:ap-northeast-2:000000000000:repository/seonjalal/production/ai-chat" }
}
override_resource {
  target          = aws_ecr_repository.ai["migrate"]
  override_during = plan
  values          = { arn = "arn:aws:ecr:ap-northeast-2:000000000000:repository/seonjalal/production/ai-migrate" }
}
override_resource {
  target          = aws_ecr_repository.ai["profiler"]
  override_during = plan
  values          = { arn = "arn:aws:ecr:ap-northeast-2:000000000000:repository/seonjalal/production/ai-profiler" }
}
override_resource {
  target          = aws_ecr_repository.ai["search"]
  override_during = plan
  values          = { arn = "arn:aws:ecr:ap-northeast-2:000000000000:repository/seonjalal/production/ai-search" }
}

run "publisher_trusts_only_ai_main_with_org_repository_ids" {
  command = plan
  assert {
    condition = (
      aws_iam_role.ai_image_publisher.name == "seonjalal-v2-production-ai-image-publisher" &&
      aws_iam_role.ai_image_publisher.max_session_duration == 3600 &&
      jsondecode(aws_iam_role.ai_image_publisher.assume_role_policy) == {
        Version = "2012-10-17"
        Statement = [{
          Effect    = "Allow"
          Action    = "sts:AssumeRoleWithWebIdentity"
          Principal = { Federated = "arn:aws:iam::000000000000:oidc-provider/token.actions.githubusercontent.com" }
          Condition = { StringEquals = {
            "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
            "token.actions.githubusercontent.com:sub" = "repo:100-hours-a-week@167328634/KTB4-12th-AI@1344623933:ref:refs/heads/main"
          } }
        }]
      }
    )
    error_message = "AI main의 불변 owner/repository ID와 STS audience만 수임 허용. PR/develop/다른 저장소 금지."
  }
}

run "publisher_can_push_only_four_production_repositories" {
  command = plan
  assert {
    condition = (
      aws_iam_role_policy.ai_image_publish.name == "PublishOnlyProductionAiImages" &&
      length(jsondecode(aws_iam_role_policy.ai_image_publish.policy).Statement) == 2 &&
      jsondecode(aws_iam_role_policy.ai_image_publish.policy).Statement[0].Effect == "Allow" &&
      toset(jsondecode(aws_iam_role_policy.ai_image_publish.policy).Statement[0].Action) == toset(["ecr:GetAuthorizationToken"]) &&
      jsondecode(aws_iam_role_policy.ai_image_publish.policy).Statement[0].Resource == "*" &&
      jsondecode(aws_iam_role_policy.ai_image_publish.policy).Statement[1].Effect == "Allow" &&
      toset(jsondecode(aws_iam_role_policy.ai_image_publish.policy).Statement[1].Resource) == toset([
        for workload in ["chat", "migrate", "profiler", "search"] :
        "arn:aws:ecr:ap-northeast-2:000000000000:repository/seonjalal/production/ai-${workload}"
      ]) &&
      toset(jsondecode(aws_iam_role_policy.ai_image_publish.policy).Statement[1].Action) == toset([
        "ecr:BatchCheckLayerAvailability", "ecr:BatchGetImage", "ecr:CompleteLayerUpload",
        "ecr:DescribeImages", "ecr:DescribeRepositories", "ecr:GetDownloadUrlForLayer",
        "ecr:InitiateLayerUpload", "ecr:PutImage", "ecr:UploadLayerPart",
      ])
    )
    error_message = "로그인 Token 외에는 운영 AI 네 저장소 업로드·조회만 허용. ECS/IAM/Secret/S3/삭제 권한 금지."
  }
}
