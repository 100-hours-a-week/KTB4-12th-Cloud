locals {
  # AI CI와 ECS Task Definition이 공유하는 스테이징 이미지 저장 계약이다.
  ai_ecr_workloads = toset([
    "chat",
    "migrate",
    "profiler",
    "search",
  ])
}

resource "aws_ecr_repository" "ai" {
  for_each = local.ai_ecr_workloads

  name                 = "seonjalal/staging/ai-${each.key}"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = false

  encryption_configuration {
    encryption_type = "AES256"
  }

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = {
    Name               = "${local.name_prefix}-ai-${each.key}"
    Component          = "container-registry"
    Workload           = "ai-${each.key}"
    Access             = "iam-only"
    Lifecycle          = "persistent"
    BackupPolicy       = "ecr-managed"
    DataClassification = "container-artifact"
  }
}

resource "aws_ecr_lifecycle_policy" "ai" {
  for_each = aws_ecr_repository.ai

  repository = each.value.name
  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
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
