locals {
  project     = "seonjalal"
  version     = "v2"
  environment = "develop"
  component   = "platform"
  name_prefix = "${local.project}-${local.version}-${local.environment}"

  foundation_network = data.terraform_remote_state.foundation.outputs.network
  app_subnet_ids     = values(local.foundation_network.app_subnet_ids)

  required_tags = {
    Project     = local.project
    Environment = local.environment
    Version     = local.version
    Component   = local.component
    ManagedBy   = "terraform"
    Owner       = "cloud"
    Repository  = "KTB4-12th-Cloud"
    CostCenter  = "ktb4-12th"
  }
}
