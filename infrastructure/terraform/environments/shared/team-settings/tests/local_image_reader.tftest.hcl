# 실제 AWS 호출 없이 로컬 이미지 읽기 사용자의 opt-in과 권한 범위 검사.
mock_provider "aws" {}

run "disabled_by_default" {
  command = plan
  assert {
    condition     = length(aws_iam_user.local_image_reader) == 0 && length(aws_iam_user_policy.local_image_reader) == 0
    error_message = "명시 활성화 전 사용자·정책 생성 금지."
  }
}

run "enabled_is_get_object_only_on_one_prefix" {
  command = plan
  variables {
    local_image_reader_enabled = true
  }
  assert {
    condition     = length(aws_iam_user.local_image_reader) == 1 && aws_iam_user.local_image_reader[0].name == "seonjalal-local-product-image-reader"
    error_message = "전용 사용자 하나만 생성 필요."
  }
  assert {
    condition = length(jsondecode(aws_iam_user_policy.local_image_reader[0].policy).Statement) == 1 && alltrue([
      for statement in jsondecode(aws_iam_user_policy.local_image_reader[0].policy).Statement :
      statement.Effect == "Allow" &&
      statement.Action == ["s3:GetObject"] &&
      statement.Resource == "arn:aws:s3:::gift-dev-product-images-721744297924-ap-northeast-2-an/test/products/*"
    ])
    error_message = "허용은 개발 상품 이미지 경로의 GetObject 하나뿐이어야 함."
  }
}
