resource "aws_secretsmanager_secret" "runtime" {
  name                    = "/seonjalal/develop/runtime"
  description             = "Runtime secrets for the Seonjalal V2 develop Docker Compose environment"
  recovery_window_in_days = 7

  tags = {
    Name               = "${local.name_prefix}-runtime"
    Component          = "runtime-secret"
    Access             = "develop-runtime-role-only"
    Lifecycle          = "persistent"
    BackupPolicy       = "secrets-manager-versioned"
    DataClassification = "secret"
  }
}
