output "runtime" {
  value = {
    service_name           = aws_ecs_service.ai.name
    task_template_arn      = aws_ecs_task_definition.app.arn
    job_task_definitions   = { for name, job in aws_ecs_task_definition.job : name => job.arn }
    namespace_arn          = local.namespace_arn
    runtime_secret_arns    = { for name, secret in aws_secretsmanager_secret.runtime : name => secret.arn }
    execution_role_arns    = { for name, role in aws_iam_role.execution : name => role.arn }
    task_role_arns         = { for name, role in aws_iam_role.task : name => role.arn }
    task_security_group_id = aws_security_group.task.id
    network_configuration = {
      subnets         = [var.config.subnet_id]
      security_groups = [aws_security_group.task.id, var.config.database_client_security_group_id]
    }
    source_sha = var.config.source_sha
  }
}
