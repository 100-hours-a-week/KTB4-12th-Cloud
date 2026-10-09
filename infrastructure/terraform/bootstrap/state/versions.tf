terraform {
  required_version = ">= 1.10.0, < 2.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.67.0"
    }
  }

  # 최초 Apply로 생성한 전용 Bucket에 Bootstrap State를 보관한다.
  # 실제 Bucket, Key와 Local Profile은 Git에서 제외된 backend.hcl로 전달한다.
  backend "s3" {}
}
