output "staging" {
  value = {
    staging_domain_name               = var.enable_staging_certificate ? local.staging_domain_name : null
    staging_certificate_arn           = try(aws_acm_certificate_validation.staging[0].certificate_arn, null)
    runtime_probe_task_definition_arn = try(aws_ecs_task_definition.runtime_probe[0].arn, null)
    runtime_secret_arn                = aws_secretsmanager_secret.backend_runtime.arn
    cluster_name                      = aws_ecs_cluster.this.name
    capacity_provider                 = aws_ecs_capacity_provider.application.name
    asg_name                          = aws_autoscaling_group.application.name
    repository_url                    = aws_ecr_repository.backend.repository_url
    alb_dns_name                      = try(aws_lb.backend[0].dns_name, null)
    task_security_group_id            = aws_security_group.task.id
    alb_security_group_id             = aws_security_group.alb.id
    service_name                      = try(aws_ecs_service.backend[0].name, null)
    log_group                         = aws_cloudwatch_log_group.backend.name
    subnet_ids                        = var.app_subnet_ids
  }
}
