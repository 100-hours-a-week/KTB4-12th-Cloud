locals {
  ai_ecr_workloads = toset(["chat", "migrate", "profiler", "search"])
}

provider "aws" {
  region              = var.aws_region
  profile             = var.aws_profile
  allowed_account_ids = [var.aws_account_id]
  default_tags {
    tags = {
      Project     = "seonjalal"
      Environment = "production"
      Component   = "ai-artifacts"
      ManagedBy   = "terraform"
    }
  }
}

# 스테이징 이미지를 공유하지 않고 동일 Release의 네 Digest를 운영 전용 저장소에 발행한다.
resource "aws_ecr_repository" "ai" {
  for_each = local.ai_ecr_workloads

  name                 = "seonjalal/production/ai-${each.key}"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = false

  encryption_configuration {
    encryption_type = "AES256"
  }

  image_scanning_configuration {
    scan_on_push = true
  }

  tags = {
    Name               = "seonjalal-v2-production-ai-${each.key}"
    Component          = "container-registry"
    Workload           = "ai-${each.key}"
    Access             = "iam-only"
    Lifecycle          = "persistent"
    BackupPolicy       = "ecr-managed"
    DataClassification = "container-artifact"
  }

  lifecycle {
    prevent_destroy = true
  }
}

# 릴리스 보존 기간이 확정되기 전에는 태그가 달린 이미지 삭제 정책을 적용하지 않는다.
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
        action = { type = "expire" }
      },
    ]
  })
}
