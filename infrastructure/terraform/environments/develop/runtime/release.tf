locals {
  initial_release_shas = {
    frontend = var.initial_frontend_sha
    backend  = var.initial_backend_sha
  }
}

resource "aws_ssm_parameter" "develop_release" {
  for_each = local.initial_release_shas

  name        = "/seonjalal/develop/release/${each.key}_sha"
  description = "Latest desired ${each.key} Git SHA for the Develop runtime"
  type        = "String"
  value       = each.value

  # 최초 생성 이후에는 Application CI가 희망 SHA를 갱신한다.
  lifecycle {
    ignore_changes = [value]
  }

  tags = {
    Name               = "${local.name_prefix}-${each.key}-desired-sha"
    Component          = "develop-release"
    Workload           = each.key
    Access             = "github-oidc-and-runtime-role"
    Lifecycle          = "persistent"
    BackupPolicy       = "not-applicable"
    DataClassification = "deployment-metadata"
  }
}
