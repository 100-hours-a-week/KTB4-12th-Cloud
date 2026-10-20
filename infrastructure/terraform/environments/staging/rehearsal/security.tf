resource "aws_security_group" "ecs_container_instance" {
  name_prefix = "${local.name_prefix}-ecs-instance-"
  description = "ECS container instances; no inbound administration port"
  vpc_id      = var.vpc_id

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

# 기존SG전체설정을인수하지않고새스테이징Source SG전용규칙만소유.
resource "aws_vpc_security_group_ingress_rule" "secrets_from_instance" {
  count                        = var.secrets_endpoint_security_group_id == null ? 0 : 1
  security_group_id            = var.secrets_endpoint_security_group_id
  referenced_security_group_id = aws_security_group.ecs_container_instance.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  description                  = "Staging ECS agent secret injection"
}
resource "aws_vpc_security_group_ingress_rule" "secrets_from_task" {
  count                        = var.secrets_endpoint_security_group_id == null ? 0 : 1
  security_group_id            = var.secrets_endpoint_security_group_id
  referenced_security_group_id = aws_security_group.task.id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  description                  = "Staging backend private secrets access"
}
resource "aws_vpc_security_group_ingress_rule" "database_from_task" {
  count                        = var.database_security_group_id == null ? 0 : 1
  security_group_id            = var.database_security_group_id
  referenced_security_group_id = aws_security_group.task.id
  ip_protocol                  = "tcp"
  from_port                    = 3306
  to_port                      = 3306
  description                  = "Staging backend access to rehearsal RDS only"
}
