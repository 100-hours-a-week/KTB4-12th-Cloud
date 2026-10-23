# MySQL 에러 로그의 CloudWatch 보존 기간을 Terraform이 직접 관리. RDS가 먼저 자동 생성하면 보존이 무기한이 됨.
# 이름은 RDS 규칙(/aws/rds/instance/<식별자>/<로그종류>)과 일치해야 하며 팀 로그 읽기 정책(team-settings)이 같은 이름을 허용.
# 슬로우 쿼리·일반 로그는 커스텀 파라미터 그룹과 재부팅이 필요해 이 변경에 포함하지 않음.
resource "aws_cloudwatch_log_group" "mysql_error" {
  count             = var.enable_database ? 1 : 0
  name              = "/aws/rds/instance/seonjalal-v2-production/error"
  retention_in_days = 30
}

# 2단계: 운영 전용 빈 DB만 준비. Source/리허설 데이터 복원·접속 전환 없음.
resource "aws_db_instance" "production" {
  count = var.enable_database ? 1 : 0

  identifier                  = "seonjalal-v2-production"
  engine                      = "mysql"
  engine_version              = "8.4.11"
  instance_class              = var.database_instance_class
  parameter_group_name        = "default.mysql8.4"
  allocated_storage           = 20
  max_allocated_storage       = var.database_max_storage_gib
  storage_type                = "gp3"
  storage_encrypted           = true
  multi_az                    = true
  publicly_accessible         = false
  port                        = 3306
  db_subnet_group_name        = aws_db_subnet_group.production.name
  vpc_security_group_ids      = [aws_security_group.database.id]
  username                    = "seonjalal_admin"
  manage_master_user_password = true
  backup_retention_period     = 7
  # 에러 로그만 내보냄. 재부팅·파라미터 그룹 변경 없이 적용되는 설정.
  enabled_cloudwatch_logs_exports = ["error"]
  backup_window                   = "19:00-19:30"
  maintenance_window              = "sun:20:00-sun:21:00"
  copy_tags_to_snapshot           = true
  deletion_protection             = true
  skip_final_snapshot             = false
  final_snapshot_identifier       = "seonjalal-v2-production-final"
  delete_automated_backups        = false
  auto_minor_version_upgrade      = false
  allow_major_version_upgrade     = false
  apply_immediately               = false

  # 관리자 비밀번호는 RDS가 Secrets Manager에 관리. Terraform 비밀값 입력 금지.
  # 스키마명은 운영 Source 확인·마이그레이션 절차에서 결정하므로 db_name 미지정.
  lifecycle {
    prevent_destroy = true
  }
  depends_on = [terraform_data.network_guard, aws_vpc_security_group_ingress_rule.database_from_backend, aws_cloudwatch_log_group.mysql_error]
}

output "production_database_candidate" {
  description = "빈 운영 DB 후보의 접속 메타데이터. 업무 데이터·앱 전환 완료 의미 아님"
  value = var.enable_database ? {
    identifier           = aws_db_instance.production[0].identifier
    address              = aws_db_instance.production[0].address
    port                 = aws_db_instance.production[0].port
    master_secret_arn    = try(aws_db_instance.production[0].master_user_secret[0].secret_arn, null)
    data_migration_ready = false
    application_ready    = false
  } : null
}
