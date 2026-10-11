locals {
  project     = "seonjalal"
  version     = "v2"
  name_prefix = "${local.project}-${local.version}-${var.environment}"

  availability_zones = [
    "${var.aws_region}a",
    "${var.aws_region}c",
  ]

  subnet_cidrs = {
    public = {
      a = "10.20.0.0/24"
      c = "10.20.1.0/24"
    }
    app = {
      a = "10.20.10.0/24"
      c = "10.20.11.0/24"
    }
    data = {
      a = "10.20.20.0/24"
      c = "10.20.21.0/24"
    }
  }

  required_tags = {
    Project     = local.project
    Environment = var.environment
    Version     = local.version
    ManagedBy   = "terraform"
    Owner       = "cloud"
    Repository  = "KTB4-12th-Cloud"
    CostCenter  = "ktb4-12th"
  }
}
