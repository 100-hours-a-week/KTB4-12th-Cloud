output "develop_runtime" {
  description = "Develop Runtime의 비민감 운영 경계"
  value = {
    availability_zone     = aws_instance.develop.availability_zone
    instance_type         = aws_instance.develop.instance_type
    iam_role_name         = aws_iam_role.develop.name
    bootstrap_version     = var.bootstrap_version
    public_ipv4_ephemeral = true
    public_ingress_rules  = 0
    access_path           = "ssm-and-tailscale"
    backup_policy         = "rebuildable-no-snapshot"
  }
}

output "develop_instance_id" {
  description = "SSM 명령 등에 사용할 Develop EC2 Instance ID"
  value       = aws_instance.develop.id
  sensitive   = true
}

output "develop_ecr" {
  description = "Develop CI가 Image를 발행할 Workload별 ECR Repository 계약"
  value = {
    for workload, repository in aws_ecr_repository.develop : workload => {
      name = repository.name
      url  = repository.repository_url
    }
  }
  sensitive = true
}

output "github_ecr_publisher_role_arns" {
  description = "각 Application Repository의 DEV_ECR_ROLE_ARN Variable에 등록할 OIDC Role ARN"
  value = {
    for workload, role in aws_iam_role.github_ecr_publisher : workload => role.arn
  }
  sensitive = true
}

output "develop_runtime_secret_name" {
  description = "배포 Script가 조회할 Develop Runtime Secrets Manager Secret 이름"
  value       = aws_secretsmanager_secret.runtime.name
}

output "github_develop_deployer_role_arns" {
  description = "FE·BE Repository의 DEV_CD_ROLE_ARN Variable에 등록할 Component별 배포 OIDC Role ARN"
  value = {
    for workload, role in aws_iam_role.github_develop_deployer : workload => role.arn
  }
  sensitive = true
}
