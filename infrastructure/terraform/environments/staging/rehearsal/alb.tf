resource "aws_security_group" "alb" {
  name_prefix = "${local.name_prefix}-alb-"
  description = "Staging ALB; approved HTTPS clients only"
  vpc_id      = var.vpc_id
  lifecycle {
    create_before_destroy = true
  }
}
resource "aws_security_group" "task" {
  name_prefix = "${local.name_prefix}-task-"
  description = "Staging backend tasks; inbound only from staging ALB"
  vpc_id      = var.vpc_id
  lifecycle {
    create_before_destroy = true
  }
}
resource "aws_vpc_security_group_ingress_rule" "alb_https" {
  count             = var.enable_alb && var.tailscale_router_instance_id != null ? 1 : 0
  security_group_id = aws_security_group.alb.id
  # Subnet Router의 기본 SNAT를 유지하면 ALB에는 진입 EC2의 Private IP로 도착.
  # Tailnet 100.x 대역 전체 또는 공유 App 서브넷 전체를 허용하지 않음.
  cidr_ipv4   = "${data.aws_instance.tailscale_router[0].private_ip}/32"
  ip_protocol = "tcp"
  from_port   = 443
  to_port     = 443
}
resource "aws_vpc_security_group_egress_rule" "alb_to_task" {
  security_group_id            = aws_security_group.alb.id
  referenced_security_group_id = aws_security_group.task.id
  ip_protocol                  = "tcp"
  from_port                    = 8080
  to_port                      = 8080
}
resource "aws_vpc_security_group_ingress_rule" "task_from_alb" {
  security_group_id            = aws_security_group.task.id
  referenced_security_group_id = aws_security_group.alb.id
  ip_protocol                  = "tcp"
  from_port                    = 8080
  to_port                      = 8080
}
resource "aws_vpc_security_group_egress_rule" "task_outbound" {
  security_group_id = aws_security_group.task.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}
resource "aws_lb" "backend" {
  count                      = var.enable_alb ? 1 : 0
  name                       = "${local.name_prefix}-alb"
  internal                   = true
  load_balancer_type         = "application"
  subnets                    = var.app_subnet_ids
  security_groups            = [aws_security_group.alb.id]
  enable_deletion_protection = false
  idle_timeout               = 60
  drop_invalid_header_fields = true
  depends_on                 = [terraform_data.network_guard, data.aws_subnet.tailscale_router]
  lifecycle {
    # Production shared-edge와 frontend가 이 ALB를 data source로 사용한다.
    # 스테이징 휴면 전환에서 enable_alb=false로 잘못 제거하는 사고를 차단한다.
    prevent_destroy = true

    precondition {
      condition     = var.alb_certificate_arn != null && var.tailscale_router_instance_id != null
      error_message = "유효한 ACM 인증서와 명시한 Tailscale 진입 EC2 확인 전 ALB 생성 금지."
    }
  }
}
resource "aws_lb_target_group" "backend" {
  count                = var.enable_alb ? 1 : 0
  name_prefix          = "stgbe-"
  port                 = 8080
  protocol             = "HTTP"
  target_type          = "ip"
  vpc_id               = var.vpc_id
  deregistration_delay = 60
  health_check {
    path                = "/actuator/health"
    matcher             = "200"
    interval            = 10
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 2
  }
  lifecycle {
    create_before_destroy = true
  }
}
resource "aws_lb_listener" "https" {
  count             = var.enable_alb ? 1 : 0
  load_balancer_arn = aws_lb.backend[0].arn
  port              = 443
  protocol          = "HTTPS"
  certificate_arn   = var.alb_certificate_arn
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.backend[0].arn
  }
}
