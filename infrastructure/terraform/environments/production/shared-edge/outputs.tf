output "shared_edge" {
  value = {
    alb_arn           = data.aws_lb.shared.arn
    listener_arn      = aws_lb_listener.production.arn
    target_group_arn  = aws_lb_target_group.backend.arn
    origin_domain     = local.origin_domain
    origin_https_port = 8443
    application_ready = false
    traffic_switched  = false
    database_switched = false
  }
}
