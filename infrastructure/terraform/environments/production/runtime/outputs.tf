output "runtime_compute" {
  value = {
    cluster_name        = data.aws_ecs_cluster.production.cluster_name
    capacity_provider   = aws_ecs_capacity_provider.production.name
    autoscaling_group   = aws_autoscaling_group.node.name
    node_security_group = aws_security_group.node.id
    instance_type       = aws_launch_template.node.instance_type
    desired_nodes       = aws_autoscaling_group.node.desired_capacity
    availability_limit  = "2개 AZ 호스트 배치 및 Task 분산은 Apply 후 확인 필요"
    application_ready   = false
    traffic_switched    = false
    database_switched   = false
  }
}

output "ai_compute" {
  value = {
    cluster_name      = data.aws_ecs_cluster.production.cluster_name
    capacity_provider = aws_ecs_capacity_provider.ai.name
    autoscaling_group = aws_autoscaling_group.ai.name
    instance_type     = aws_launch_template.ai.instance_type
    subnet_id         = local.ai_app_subnet_id
    desired_nodes     = aws_autoscaling_group.ai.desired_capacity
  }
}
