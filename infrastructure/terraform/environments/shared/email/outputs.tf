output "ses" {
  description = "SMTP 호스트와 발신 설정. 비밀 값은 포함하지 않는다."
  value = {
    region              = var.aws_region
    smtp_host           = "email-smtp.${var.aws_region}.amazonaws.com"
    smtp_port           = 587
    from_address        = var.from_address
    domain_identity_arn = aws_sesv2_email_identity.domain.arn
    configuration_set   = aws_sesv2_configuration_set.transactional.configuration_set_name
    smtp_iam_user_names = { for k, u in aws_iam_user.smtp : k => u.name }
    dkim_status_hint    = "apply 후 aws sesv2 get-email-identity로 DKIM 상태가 SUCCESS인지 확인"
  }
}
