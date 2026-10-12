locals {
  # Repository 경로는 CI와 CD가 공유하는 배포 계약이다. AI는 Dockerfile 확정 전까지 비워 둔다.
  develop_ecr_repositories = toset([
    "frontend",
    "backend",
    "ai",
  ])
}

resource "aws_ecr_repository" "develop" {
  for_each = local.develop_ecr_repositories

  name                 = "${local.project}/${local.environment}/${each.key}"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = false

  encryption_configuration {
    encryption_type = "AES256"
  }

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = {
    Name               = "${local.name_prefix}-${each.key}"
    Component          = "container-registry"
    Workload           = each.key
    Access             = "iam-only"
    Lifecycle          = "persistent"
    BackupPolicy       = "ecr-managed"
    DataClassification = "container-artifact"
  }
}

resource "aws_ecr_lifecycle_policy" "develop" {
  for_each = aws_ecr_repository.develop

  repository = each.value.name
  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Keep the latest 30 develop commit images"
        selection = {
          tagStatus     = "tagged"
          tagPrefixList = ["develop-"]
          countType     = "imageCountMoreThan"
          countNumber   = 30
        }
        action = {
          type = "expire"
        }
      },
      {
        rulePriority = 2
        description  = "Remove untagged images after 7 days"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 7
        }
        action = {
          type = "expire"
        }
      },
    ]
  })
}
