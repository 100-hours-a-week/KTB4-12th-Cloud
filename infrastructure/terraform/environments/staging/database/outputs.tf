output "staging_database" {
  description = "스테이징 MySQL 접속 메타데이터. 스키마 버전·데이터 성격 확인 전에는 앱 연결 준비 완료가 아니다."
  value = var.enable_database ? {
    identifier                   = aws_db_instance.staging[0].identifier
    address                      = aws_db_instance.staging[0].address
    port                         = aws_db_instance.staging[0].port
    master_secret_arn            = try(aws_db_instance.staging[0].master_user_secret[0].secret_arn, null)
    security_group_id            = aws_security_group.database[0].id
    schema_version_verified      = false
    data_classification_verified = false
    application_ready            = false
  } : null
}
