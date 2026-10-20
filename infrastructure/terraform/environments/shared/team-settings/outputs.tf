output "team_settings" {
  value = {
    settings_admin_enabled = var.team_settings_admin_enabled
    users                  = local.teams
    groups                 = { for team, group in aws_iam_group.team : team => group.name }
    secret_names           = { for key, secret in aws_secretsmanager_secret.team : key => secret.name }
    parameter_prefixes     = { for team in keys(local.teams) : team => [for env in ["develop", "staging"] : "/seonjalal/${env}/config/${team}/"] }
    login_profiles_ready   = false
    runtime_connected      = false
    theo_admin_removed     = false
  }
}
