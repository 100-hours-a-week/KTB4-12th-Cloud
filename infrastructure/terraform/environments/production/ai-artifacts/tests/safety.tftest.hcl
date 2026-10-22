# 실제 AWS 접속 없이 운영 Repository 범위와 보존 정책 검증.
mock_provider "aws" {}

variables {
  aws_account_id = "000000000000"
  aws_profile    = null
}

run "four_production_repositories_are_private_and_immutable" {
  command = plan
  assert {
    condition = (
      local.ai_ecr_workloads == toset(["chat", "migrate", "profiler", "search"]) &&
      length(aws_ecr_repository.ai) == 4 &&
      alltrue([for workload, repository in aws_ecr_repository.ai :
        repository.name == "seonjalal/production/ai-${workload}" &&
        repository.image_tag_mutability == "IMMUTABLE" &&
        !repository.force_delete &&
        repository.encryption_configuration[0].encryption_type == "AES256" &&
        repository.image_scanning_configuration[0].scan_on_push &&
        repository.tags.Component == "container-registry" &&
        repository.tags.Workload == "ai-${workload}"
      ])
    )
    error_message = "운영 AI 네 Repository만 Immutable·스캔·암호화·강제 삭제 금지로 생성 필요."
  }
}

run "only_untagged_images_expire_after_seven_days" {
  command = plan
  assert {
    condition = (
      length(aws_ecr_lifecycle_policy.ai) == 4 &&
      alltrue([for workload, lifecycle in aws_ecr_lifecycle_policy.ai :
        lifecycle.repository == "seonjalal/production/ai-${workload}" &&
        length(jsondecode(lifecycle.policy).rules) == 1 &&
        jsondecode(lifecycle.policy).rules[0].selection.tagStatus == "untagged" &&
        jsondecode(lifecycle.policy).rules[0].selection.countType == "sinceImagePushed" &&
        jsondecode(lifecycle.policy).rules[0].selection.countUnit == "days" &&
        jsondecode(lifecycle.policy).rules[0].selection.countNumber == 7 &&
        jsondecode(lifecycle.policy).rules[0].action.type == "expire"
      ])
    )
    error_message = "태그된 운영 Release를 자동 삭제하지 않고 미태그 이미지만 7일 만료 필요."
  }
}

run "other_region_is_rejected" {
  command = plan
  variables { aws_region = "us-east-1" }
  expect_failures = [var.aws_region]
}
