output "production_foundation" {
  value = {
    cluster_name               = aws_ecs_cluster.production.name
    repository_url             = aws_ecr_repository.backend.repository_url
    runtime_secret_arn         = aws_secretsmanager_secret.runtime.arn
    log_group                  = aws_cloudwatch_log_group.backend.name
    alb_security_group_id      = aws_security_group.alb.id
    task_security_group_id     = aws_security_group.backend.id
    database_security_group_id = aws_security_group.database.id
    database_subnet_group      = aws_db_subnet_group.production.name
    application_ready          = false
    database_ready             = false
    production_cutover_ready   = false
  }
}
