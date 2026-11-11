variable "local_image_reader_enabled" {
  type        = bool
  default     = false
  description = "팀 로컬 백엔드가 개발 상품 이미지 서명 URL을 만들 때 쓰는 읽기 전용 IAM 사용자 생성. 액세스 키는 Terraform에서 만들지 않음."
}

locals {
  local_image_reader_name = "seonjalal-local-product-image-reader"
  # 서명 URL 생성에는 GetObject만 필요. 와일드카드 버킷·목록·쓰기·삭제 권한 없음.
  local_image_reader_resource = "${local.product_image_bucket_arn}/test/products/*"
}

# 로그인(콘솔 비밀번호)이 없는 프로그램 전용 사용자. 액세스 키는 State에 비밀값이 남지 않도록 별도 절차로 발급.
resource "aws_iam_user" "local_image_reader" {
  count         = var.local_image_reader_enabled ? 1 : 0
  name          = local.local_image_reader_name
  force_destroy = false
  tags          = { Purpose = "local-dev-product-image-read-only" }
}

resource "aws_iam_user_policy" "local_image_reader" {
  count = var.local_image_reader_enabled ? 1 : 0
  name  = "ReadDevelopProductImagesOnly"
  user  = aws_iam_user.local_image_reader[0].name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # 개발 상품 이미지 경로의 객체 읽기만 허용.
        Sid      = "ReadDevelopProductImages"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = local.local_image_reader_resource
      },
    ]
  })
}

output "local_image_reader" {
  value = {
    enabled      = var.local_image_reader_enabled
    user         = local.local_image_reader_name
    scope        = local.local_image_reader_resource
    access_level = "s3:GetObject only; no list, write, delete, or any other service"
    access_key   = "Terraform에서 생성하지 않음. 별도 절차로 발급하고 주기적으로 교체"
  }
}
