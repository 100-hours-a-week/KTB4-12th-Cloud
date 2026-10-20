output "certificates" {
  description = "검증 완료 인증서 메타데이터. 서비스 연결/트래픽 전환 완료를 의미하지 않음."
  value = {
    origin_arn        = aws_acm_certificate_validation.origin.certificate_arn
    origin_domain     = local.origin_domain
    viewer_arn        = aws_acm_certificate_validation.viewer.certificate_arn
    viewer_domains    = local.viewer_domains
    application_ready = false
    traffic_switched  = false
    database_switched = false
  }
}
