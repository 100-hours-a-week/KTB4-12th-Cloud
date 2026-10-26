resource "aws_ecs_cluster" "this" {
  name       = "${local.name_prefix}-cluster"
  depends_on = [terraform_data.network_guard]

  setting {
    name  = "containerInsights"
    value = "disabled"
  }
}

resource "aws_ecs_cluster_capacity_providers" "this" {
  cluster_name = aws_ecs_cluster.this.name

  # AI 호스트가 켜져 있을 때만 AI 전용 용량 공급자를 함께 연결한다. 기본 전략은 BE 공급자 그대로.
  capacity_providers = concat([aws_ecs_capacity_provider.application.name], aws_ecs_capacity_provider.ai[*].name)

  default_capacity_provider_strategy {
    capacity_provider = aws_ecs_capacity_provider.application.name
    base              = 1
    weight            = 1
  }
}
