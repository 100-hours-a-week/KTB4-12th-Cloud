locals {
  project     = "seonjalal"
  version     = "v2"
  environment = "develop"
  component   = "runtime"
  name_prefix = "${local.project}-${local.version}-${local.environment}"

  foundation_network = data.terraform_remote_state.foundation.outputs.network
  public_subnet_a_id = try(local.foundation_network.public_subnet_ids["a"], null)

  required_tags = {
    Project            = local.project
    Environment        = local.environment
    Version            = local.version
    Component          = local.component
    ManagedBy          = "terraform"
    Owner              = "cloud"
    Repository         = "KTB4-12th-Cloud"
    CostCenter         = "ktb4-12th"
    Access             = "tailscale-only"
    Lifecycle          = "on-demand"
    BackupPolicy       = "rebuildable-no-snapshot"
    DataClassification = "synthetic-test"
  }
}
