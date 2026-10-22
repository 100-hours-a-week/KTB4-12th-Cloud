output "ai_database" {
  description = "DB 접속 메타데이터. pgvector·역할·Migration·앱 기동 완료 여부는 별도 검증"
  value = {
    identifier               = aws_db_instance.ai.identifier
    host                     = aws_db_instance.ai.address
    port                     = aws_db_instance.ai.port
    database_name            = aws_db_instance.ai.db_name
    master_secret_arn        = aws_db_instance.ai.master_user_secret[0].secret_arn
    database_security_group  = aws_security_group.database.id
    client_security_group_id = aws_security_group.client.id
    subnet_group_name        = aws_db_subnet_group.ai.name
  }
}
