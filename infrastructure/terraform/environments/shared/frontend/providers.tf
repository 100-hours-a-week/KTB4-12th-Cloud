# CloudFront 보기용 ACM 인증서는 us-east-1에서만 사용할 수 있다.
provider "aws" {
  alias               = "us_east_1"
  region              = "us-east-1"
  profile             = var.aws_profile
  allowed_account_ids = [var.aws_account_id]

  default_tags {
    tags = {
      Project     = "seonjalal"
      Environment = "shared"
      Component   = "frontend-static"
      ManagedBy   = "terraform"
    }
  }
}

provider "aws" {
  region              = var.aws_region
  profile             = var.aws_profile
  allowed_account_ids = [var.aws_account_id]

  default_tags {
    tags = {
      Project     = "seonjalal"
      Environment = "shared"
      Component   = "frontend-static"
      ManagedBy   = "terraform"
    }
  }
}
