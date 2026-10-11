resource "aws_ssm_document" "develop_reconcile" {
  name            = "${local.name_prefix}-reconcile"
  document_type   = "Command"
  document_format = "YAML"

  content = yamlencode({
    schemaVersion = "2.2"
    description   = "Reconcile the Seonjalal Develop runtime with desired FE and BE image SHAs"
    mainSteps = [
      {
        action = "aws:runShellScript"
        name   = "reconcileDevelopRuntime"
        inputs = {
          runCommand = [
            "sudo -u ec2-user /opt/seonjalal-develop/scripts/reconcile.sh /opt/seonjalal-develop",
          ]
        }
      },
    ]
  })

  tags = {
    Name               = "${local.name_prefix}-reconcile"
    Component          = "develop-release"
    Access             = "github-oidc"
    Lifecycle          = "persistent"
    BackupPolicy       = "not-applicable"
    DataClassification = "deployment-metadata"
  }
}
