resource "aws_security_group" "ecs_container_instance" {
  name_prefix = "${local.name_prefix}-ecs-instance-"
  description = "ECS container instances; no inbound administration port"
  vpc_id      = local.foundation_network.vpc_id

  tags = {
    Name = "${local.name_prefix}-ecs-instance-sg"
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_security_group_egress_rule" "ecs_container_instance_all" {
  security_group_id = aws_security_group.ecs_container_instance.id
  description       = "ECS control plane, ECR, Logs, SSM and external dependency access through NAT"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}
