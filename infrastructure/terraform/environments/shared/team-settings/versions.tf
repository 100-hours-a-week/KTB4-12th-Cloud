terraform {
  required_version = ">= 1.10.0, < 2.0.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 6.67.0" }
  }
  backend "s3" {}
}

provider "aws" {
  region              = "ap-northeast-2"
  profile             = "seonjalal-production"
  allowed_account_ids = ["721744297924"]
  default_tags {
    tags = { Project = "seonjalal", ManagedBy = "terraform", Component = "team-settings" }
  }
}
