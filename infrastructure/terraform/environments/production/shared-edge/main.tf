locals {
  origin_domain = "v2-origin.seonjalal.com"
}

data "aws_lb" "shared" {
  name = "seonjalal-v2-staging-alb"
}

data "aws_security_group" "backend" {
  name   = "seonjalal-v2-production-backend"
  vpc_id = data.aws_lb.shared.vpc_id
}

data "aws_acm_certificate" "origin" {
  domain   = local.origin_domain
  statuses = ["ISSUED"]
  types    = ["AMAZON_ISSUED"]
}

data "aws_route53_zone" "public" {
  name         = "seonjalal.com"
  private_zone = false
}

# 기존 Staging TG와 독립. 아직 ECS 태스크 등록/앱 기동 없음.
resource "aws_lb_target_group" "backend" {
  name_prefix          = "prdbe-"
  target_type          = "ip"
  port                 = 8080
  protocol             = "HTTP"
  vpc_id               = data.aws_lb.shared.vpc_id
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

# 기존 443 Listener는 관리하지 않음. 8443은 원본 연결용이며 사용자 포트 아님.
# CloudFront 연결 전까지 이 포트의 SG 인바운드를 열지 않음.
resource "aws_lb_listener" "production" {
  load_balancer_arn = data.aws_lb.shared.arn
  port              = 8443
  protocol          = "HTTPS"
  certificate_arn   = data.aws_acm_certificate.origin.arn
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  default_action {
    type = "fixed-response"
    fixed_response {
      content_type = "text/plain"
      message_body = "Not found"
      status_code  = "404"
    }
  }
  lifecycle {
    precondition {
      condition = (
        data.aws_lb.shared.internal &&
        data.aws_lb.shared.load_balancer_type == "application" &&
        length(data.aws_lb.shared.security_groups) == 1
      )
      error_message = "기존 internal ALB 및 단일 SG 확인 필요. 예상과 다르면 적용 금지."
    }
  }
}

# 운영 원본 Host만 운영 TG로 전달. Staging으로 연결되는 규칙 없음.
resource "aws_lb_listener_rule" "production" {
  listener_arn = aws_lb_listener.production.arn
  priority     = 10
  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.backend.arn
  }
  condition {
    host_header {
      values = [local.origin_domain]
    }
  }
}

# 기존 공유 ALB SG에 운영 Backend 경로만 추가. 기존 규칙 교체/삭제 없음.
resource "aws_vpc_security_group_egress_rule" "alb_to_backend" {
  security_group_id            = one(data.aws_lb.shared.security_groups)
  referenced_security_group_id = data.aws_security_group.backend.id
  ip_protocol                  = "tcp"
  from_port                    = 8080
  to_port                      = 8080
}

resource "aws_vpc_security_group_ingress_rule" "backend_from_alb" {
  security_group_id            = data.aws_security_group.backend.id
  referenced_security_group_id = one(data.aws_lb.shared.security_groups)
  ip_protocol                  = "tcp"
  from_port                    = 8080
  to_port                      = 8080
}

# 원본 전용 이름만 추가. www/apex의 사용자 트래픽 레코드는 변경하지 않음.
# ALB는 internal 유지, DNS가 공개 Zone에 있어도 인터넷 직접 접근은 불가.
resource "aws_route53_record" "origin" {
  zone_id         = data.aws_route53_zone.public.zone_id
  name            = local.origin_domain
  type            = "A"
  allow_overwrite = false
  alias {
    name                   = data.aws_lb.shared.dns_name
    zone_id                = data.aws_lb.shared.zone_id
    evaluate_target_health = false
  }
}
