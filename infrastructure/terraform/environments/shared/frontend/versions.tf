terraform {
  required_version = ">= 1.10.0, < 2.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.67.0"
    }
  }

  # 스테이징과 운영이 공유하는 버킷의 독립 State.
  backend "s3" {}
}
