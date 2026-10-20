output "ecs" {
  description = "후속 Main, Payment, AI Service Root가 참조할 ECS Platform 경계"
  value = {
    cluster_arn                  = aws_ecs_cluster.this.arn
    cluster_name                 = aws_ecs_cluster.this.name
    application_capacity_name    = aws_ecs_capacity_provider.application.name
    application_asg_name         = aws_autoscaling_group.application.name
    container_instance_sg_id     = aws_security_group.ecs_container_instance.id
    application_app_subnet_ids   = local.app_subnet_ids
    task_security_group_id       = local.foundation_network.security_group_ids.ecs
    load_balancer_security_group = local.foundation_network.security_group_ids.alb
  }
}
