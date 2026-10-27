terraform {
  required_version = ">= 1.10.0, < 2.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.67.0"
    }
  }

  # 스테이징·운영이 함께 쓰는 발신 도메인의 독립 State.
  backend "s3" {}
}
