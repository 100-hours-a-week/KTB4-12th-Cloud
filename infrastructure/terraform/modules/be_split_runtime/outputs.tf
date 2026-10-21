output "runtime" {
  value = {
    service_names           = { for key, service in aws_ecs_service.service : key => service.name }
    task_template_arns      = { for key, task in aws_ecs_task_definition.service : key => task.arn }
    task_security_group_ids = { for key, sg in aws_security_group.task : key => sg.id }
    target_group_arn        = aws_lb_target_group.main.arn
    runtime_secret_arn      = aws_secretsmanager_secret.runtime.arn
    namespace_arn           = var.config.namespace_arn
    source_sha              = var.config.source_sha
    traffic_cutover         = var.config.traffic_cutover
  }
}
