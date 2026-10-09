terraform {
  required_version = ">= 1.7.0"

  # 실제 Bucket, Key와 Local Profile은 Git에서 제외된 backend.hcl로 전달한다.
  backend "s3" {}

  required_providers {
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.7"
    }
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region              = var.aws_region
  allowed_account_ids = [var.aws_account_id]

  default_tags {
    tags = {
      Project     = "seonjalal"
      Environment = var.environment
      ManagedBy   = "terraform"
      Component   = "alerting"
    }
  }
}
