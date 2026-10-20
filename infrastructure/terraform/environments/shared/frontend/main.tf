locals {
  bucket_name = "seonjalal-v2-frontend-${var.aws_account_id}-${var.aws_region}"
  environment_prefixes = {
    staging    = "frontend/staging/"
    production = "frontend/production/"
  }
}

# 환경별 Prefix는 객체 이름으로 구분한다. 빈 폴더 객체나 공개 권한은 생성하지 않는다.
# 버킷은 공용 State에서 소유하며 스테이징 정리 시 운영 파일까지 삭제하지 않도록 보호한다.
resource "aws_s3_bucket" "frontend" {
  bucket        = local.bucket_name
  force_destroy = false

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_public_access_block" "frontend" {
  bucket                  = aws_s3_bucket.frontend.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "frontend" {
  bucket = aws_s3_bucket.frontend.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "frontend" {
  bucket = aws_s3_bucket.frontend.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# 정적 파일의 실수 덮어쓰기와 삭제에 대비해 이전 객체 버전을 보존한다.
resource "aws_s3_bucket_versioning" "frontend" {
  bucket = aws_s3_bucket.frontend.id
  versioning_configuration {
    status = "Enabled"
  }
}
