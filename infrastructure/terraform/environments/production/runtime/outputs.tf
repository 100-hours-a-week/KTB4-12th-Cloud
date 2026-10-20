output "runtime_compute" {
  value = {
    cluster_name        = data.aws_ecs_cluster.production.cluster_name
    capacity_provider   = aws_ecs_capacity_provider.production.name
    autoscaling_group   = aws_autoscaling_group.node.name
    node_security_group = aws_security_group.node.id
    instance_type       = aws_launch_template.node.instance_type
    desired_nodes       = aws_autoscaling_group.node.desired_capacity
    availability_limit  = "단일 호스트: 호스트 장애 시 무중단 보장 없음"
    application_ready   = false
    traffic_switched    = false
    database_switched   = false
  }
}
