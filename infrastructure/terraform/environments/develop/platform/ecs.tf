resource "aws_ecs_cluster" "this" {
  name = "${local.name_prefix}-cluster"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_ecs_cluster_capacity_providers" "this" {
  cluster_name = aws_ecs_cluster.this.name

  capacity_providers = [aws_ecs_capacity_provider.application.name]

  default_capacity_provider_strategy {
    capacity_provider = aws_ecs_capacity_provider.application.name
    base              = 1
    weight            = 1
  }
}
