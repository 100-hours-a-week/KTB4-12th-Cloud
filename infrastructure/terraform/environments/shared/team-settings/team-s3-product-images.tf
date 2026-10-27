variable "team_product_image_s3_enabled" {
  type        = bool
  default     = false
  description = "MFA 인증 팀원에게 개발 상품 이미지 버킷 하나의 객체 읽기·쓰기·삭제를 허용. 다른 버킷과 버킷 설정 변경은 허용하지 않음."
}

locals {
  # 로컬 BE 테스트가 읽는 개발 상품 이미지 버킷. 와일드카드 없이 정확한 이름만 허용.
  product_image_bucket_name = "gift-dev-product-images-721744297924-ap-northeast-2-an"
  product_image_bucket_arn  = "arn:aws:s3:::${local.product_image_bucket_name}"
}

# 팀별 설정 정책과 분리한 공통 관리형 정책. 기존 Secret/SSM/로그 권한은 변경하지 않음.
resource "aws_iam_policy" "team_product_image_s3" {
  count       = var.team_product_image_s3_enabled ? 1 : 0
  name        = "SeonjalalTeamProductImageS3"
  description = "MFA read/write/delete access to the develop product image bucket only; no bucket configuration changes"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # 버킷 안 객체 목록 조회.
        Sid      = "ListProductImageBucket"
        Effect   = "Allow"
        Action   = ["s3:ListBucket", "s3:GetBucketLocation", "s3:ListBucketMultipartUploads"]
        Resource = local.product_image_bucket_arn
        Condition = {
          Bool         = { "aws:MultiFactorAuthPresent" = "true" }
          StringEquals = { "aws:RequestedRegion" = "ap-northeast-2" }
        }
      },
      {
        # 객체 읽기·업로드·삭제.
        Sid      = "ReadWriteDeleteProductImages"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload", "s3:ListMultipartUploadParts"]
        Resource = "${local.product_image_bucket_arn}/*"
        Condition = {
          Bool         = { "aws:MultiFactorAuthPresent" = "true" }
          StringEquals = { "aws:RequestedRegion" = "ap-northeast-2" }
        }
      },
      {
        # 콘솔의 버킷 목록 표시용. 리소스 지정이 불가능한 API이며 버킷 이름만 보임.
        Sid      = "ListBucketNamesForConsole"
        Effect   = "Allow"
        Action   = ["s3:ListAllMyBuckets"]
        Resource = "*"
        Condition = {
          Bool = { "aws:MultiFactorAuthPresent" = "true" }
        }
      },
      {
        # 버킷 정책·접근 차단·ACL·수명주기 같은 버킷 설정은 Terraform 소유. 팀이 바꾸지 못하도록 명시 차단.
        Sid    = "DenyBucketConfigurationChanges"
        Effect = "Deny"
        Action = [
          "s3:PutBucketPolicy", "s3:DeleteBucketPolicy", "s3:PutBucketAcl",
          "s3:PutBucketPublicAccessBlock", "s3:PutBucketOwnershipControls",
          "s3:PutLifecycleConfiguration", "s3:PutBucketVersioning", "s3:PutEncryptionConfiguration",
          "s3:PutBucketCORS", "s3:DeleteBucket", "s3:PutObjectAcl",
        ]
        Resource = [local.product_image_bucket_arn, "${local.product_image_bucket_arn}/*"]
      },
    ]
  })
}

resource "aws_iam_group_policy_attachment" "team_product_image_s3" {
  for_each   = var.team_product_image_s3_enabled ? local.teams : {}
  group      = aws_iam_group.team[each.key].name
  policy_arn = aws_iam_policy.team_product_image_s3[0].arn
}

output "team_product_image_s3" {
  value = {
    enabled      = var.team_product_image_s3_enabled
    bucket       = local.product_image_bucket_name
    mfa_required = true
    access_level = "object read/write/delete on one bucket; no other buckets, no bucket configuration"
  }
}
