# 실제 AWS 호출 없이 팀 상품 이미지 S3 정책의 opt-in·범위·MFA 조건 검사.
mock_provider "aws" {}

run "disabled_by_default" {
  command = plan
  assert {
    condition     = length(aws_iam_policy.team_product_image_s3) == 0 && length(aws_iam_group_policy_attachment.team_product_image_s3) == 0
    error_message = "명시 활성화 전 S3 정책 생성/연결 금지."
  }
}

run "enabled_for_both_teams_one_bucket_only" {
  command = plan
  variables {
    team_product_image_s3_enabled = true
  }
  assert {
    condition     = length(aws_iam_policy.team_product_image_s3) == 1 && toset(keys(aws_iam_group_policy_attachment.team_product_image_s3)) == toset(["fullstack", "ai"])
    error_message = "공통 관리형 정책 하나를 기존 두 그룹에만 연결 필요."
  }
  assert {
    condition = alltrue([for statement in jsondecode(aws_iam_policy.team_product_image_s3[0].policy).Statement :
      statement.Effect != "Allow" || try(statement.Condition.Bool["aws:MultiFactorAuthPresent"], "false") == "true"
    ])
    error_message = "모든 Allow에 MFA 조건 필요."
  }
  assert {
    # 버킷·객체 Allow는 개발 상품 이미지 버킷 하나만. 와일드카드 버킷 금지. 전체 목록 Allow는 ListAllMyBuckets 하나만 허용.
    condition = alltrue([for statement in jsondecode(aws_iam_policy.team_product_image_s3[0].policy).Statement :
      statement.Effect != "Allow" || statement.Sid == "ListBucketNamesForConsole" ||
      startswith(statement.Resource, "arn:aws:s3:::gift-dev-product-images-721744297924-ap-northeast-2-an")
    ])
    error_message = "Allow 범위는 개발 상품 이미지 버킷 하나로 제한 필요."
  }
  assert {
    condition = alltrue([for statement in jsondecode(aws_iam_policy.team_product_image_s3[0].policy).Statement :
      statement.Effect != "Allow" || !contains(flatten([statement.Action]), "s3:*")
    ])
    error_message = "s3:* 와일드카드 금지."
  }
  assert {
    condition = anytrue([for statement in jsondecode(aws_iam_policy.team_product_image_s3[0].policy).Statement :
      statement.Effect == "Deny" && contains(statement.Action, "s3:PutBucketPolicy")
    ])
    error_message = "버킷 정책 변경 명시 차단 필요."
  }
}
