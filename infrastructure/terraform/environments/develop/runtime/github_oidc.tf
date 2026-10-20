locals {
  # 각 Repository는 자신의 Develop ECR에만 Image를 발행할 수 있다.
  github_image_publishers = {
    frontend = {
      repository    = "100-hours-a-week/KTB4-12th-FE"
      owner_id      = "167328634"
      repository_id = "1344623849"
      branch        = "develop"
      ecr_workloads = ["frontend"]
    }
    backend = {
      repository    = "100-hours-a-week/KTB4-12th-BE"
      owner_id      = "167328634"
      repository_id = "1344623893"
      branch        = "develop"
      ecr_workloads = ["backend"]
    }
    ai = {
      repository    = "100-hours-a-week/KTB4-12th-AI"
      owner_id      = "167328634"
      repository_id = "1344623933"
      branch        = "main"
      ecr_workloads = ["ai-profiling", "ai-product-search"]
    }
  }
}

data "aws_iam_openid_connect_provider" "github" {
  url = "https://token.actions.githubusercontent.com"
}

data "aws_iam_policy_document" "github_ecr_assume_role" {
  for_each = local.github_image_publishers

  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [data.aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values = [
        "repo:100-hours-a-week@${each.value.owner_id}/${split("/", each.value.repository)[1]}@${each.value.repository_id}:ref:refs/heads/${each.value.branch}",
      ]
    }
  }
}

resource "aws_iam_role" "github_ecr_publisher" {
  for_each = local.github_image_publishers

  name                 = "${local.name_prefix}-${each.key}-ecr-publisher"
  description          = "${each.value.repository} develop branch publishes only the ${each.key} image"
  assume_role_policy   = data.aws_iam_policy_document.github_ecr_assume_role[each.key].json
  max_session_duration = 3600

  tags = {
    Name               = "${local.name_prefix}-${each.key}-ecr-publisher"
    Component          = "github-oidc"
    Workload           = each.key
    GitHubRepo         = each.value.repository
    Access             = "github-oidc"
    Lifecycle          = "persistent"
    BackupPolicy       = "not-applicable"
    DataClassification = "none"
  }
}

data "aws_iam_policy_document" "github_ecr_publish" {
  for_each = local.github_image_publishers

  statement {
    sid       = "GetEcrAuthorizationToken"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid    = "PublishOnlyOwnDevelopImage"
    effect = "Allow"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:CompleteLayerUpload",
      "ecr:DescribeImages",
      "ecr:GetDownloadUrlForLayer",
      "ecr:InitiateLayerUpload",
      "ecr:PutImage",
      "ecr:UploadLayerPart",
    ]
    resources = [
      for workload in each.value.ecr_workloads : aws_ecr_repository.develop[workload].arn
    ]
  }
}

resource "aws_iam_role_policy" "github_ecr_publish" {
  for_each = local.github_image_publishers

  name   = "${local.name_prefix}-${each.key}-ecr-publish"
  role   = aws_iam_role.github_ecr_publisher[each.key].id
  policy = data.aws_iam_policy_document.github_ecr_publish[each.key].json
}

locals {
  # AI Runtime 계약 전에는 FE와 BE만 자동 배포 권한을 가진다.
  github_develop_deployers = {
    for workload, publisher in local.github_image_publishers : workload => publisher
    if contains(["frontend", "backend"], workload)
  }
}

# 각 Application Repository의 develop Branch만 자신의 Develop 배포 Role을 수임할 수 있다.
data "aws_iam_policy_document" "github_develop_deploy_assume_role" {
  for_each = local.github_develop_deployers

  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [data.aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values = [
        "repo:100-hours-a-week@${each.value.owner_id}/${split("/", each.value.repository)[1]}@${each.value.repository_id}:ref:refs/heads/${each.value.branch}",
      ]
    }
  }
}

resource "aws_iam_role" "github_develop_deployer" {
  for_each = local.github_develop_deployers

  name                 = "${local.name_prefix}-${each.key}-deployer"
  description          = "${each.value.repository} develop branch reconciles only its desired image in Develop"
  assume_role_policy   = data.aws_iam_policy_document.github_develop_deploy_assume_role[each.key].json
  max_session_duration = 3600

  tags = {
    Name               = "${local.name_prefix}-${each.key}-deployer"
    Component          = "github-oidc"
    Workload           = each.key
    GitHubRepo         = each.value.repository
    Access             = "github-oidc"
    Lifecycle          = "persistent"
    BackupPolicy       = "not-applicable"
    DataClassification = "none"
  }
}

data "aws_iam_policy_document" "github_develop_deploy" {
  for_each = local.github_develop_deployers

  statement {
    sid    = "ManageOnlyOwnDesiredImage"
    effect = "Allow"
    actions = [
      "ssm:GetParameter",
      "ssm:PutParameter",
    ]
    resources = [aws_ssm_parameter.develop_release[each.key].arn]
  }

  statement {
    sid     = "RunOnlyDevelopReconcileDocument"
    effect  = "Allow"
    actions = ["ssm:SendCommand"]
    resources = [
      aws_ssm_document.develop_reconcile.arn,
      aws_instance.develop.arn,
    ]
  }

  # Command ID는 실행 시점에 생성되므로 조회 API는 Resource ARN으로 사전 제한할 수 없다.
  statement {
    sid    = "ReadDevelopCommandResult"
    effect = "Allow"
    actions = [
      "ssm:GetCommandInvocation",
      "ssm:ListCommandInvocations",
      "ssm:DescribeInstanceInformation",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "github_develop_deploy" {
  for_each = local.github_develop_deployers

  name   = "${local.name_prefix}-${each.key}-deploy"
  role   = aws_iam_role.github_develop_deployer[each.key].id
  policy = data.aws_iam_policy_document.github_develop_deploy[each.key].json
}
