terraform {
  required_version = ">= 1.10.0, < 2.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.67.0"
    }
  }

  # 실제 Bucket 생성 전에는 `terraform init -backend=false`로 정적 검증만 수행한다.
  backend "s3" {}
}
