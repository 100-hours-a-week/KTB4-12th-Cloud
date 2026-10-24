# 실제 AWS 호출 없이 운영 동일 구성·격리·데이터 보호·비활성 기본값 계약 검증.
mock_provider "aws" {
  mock_data "aws_route_table" {
    defaults = { vpc_id = "vpc-staging", routes = [] }
  }
}

override_data {
  target = data.aws_subnet.data["subnet-data-a"]
  values = { vpc_id = "vpc-staging", availability_zone = "ap-northeast-2a", map_public_ip_on_launch = false }
}
override_data {
  target = data.aws_subnet.data["subnet-data-c"]
  values = { vpc_id = "vpc-staging", availability_zone = "ap-northeast-2c", map_public_ip_on_launch = false }
}

variables {
  aws_account_id  = "000000000000"
  aws_profile     = null
  vpc_id          = "vpc-staging"
  data_subnet_ids = ["subnet-data-a", "subnet-data-c"]
}

run "disabled_by_default_creates_nothing" {
  command = plan
  assert {
    condition = (
      length(aws_db_instance.staging) == 0 &&
      length(aws_db_subnet_group.staging) == 0 &&
      length(aws_security_group.database) == 0 &&
      length(aws_db_parameter_group.staging) == 0 &&
      length(aws_cloudwatch_log_group.mysql_error) == 0 &&
      output.staging_database == null
    )
    error_message = "enable_database 기본값(false)에서는 어떤 리소스도 만들지 않아야 함."
  }
}

run "enabled_mirrors_production_sizing_and_protection" {
  command = plan
  variables {
    enable_database      = true
    snapshot_identifier  = "seonjalal-rehearsal-final-test"
    snapshot_kms_key_arn = "arn:aws:kms:ap-northeast-2:000000000000:key/test"
  }
  assert {
    condition = (
      aws_db_instance.staging[0].identifier == "seonjalal-v2-staging" &&
      aws_db_instance.staging[0].snapshot_identifier == "seonjalal-rehearsal-final-test" &&
      aws_db_instance.staging[0].engine == "mysql" &&
      aws_db_instance.staging[0].engine_version == "8.4.11" &&
      aws_db_instance.staging[0].instance_class == "db.t4g.small" &&
      aws_db_instance.staging[0].multi_az &&
      !aws_db_instance.staging[0].publicly_accessible &&
      aws_db_instance.staging[0].port == 3306 &&
      aws_db_instance.staging[0].storage_type == "gp3" &&
      aws_db_instance.staging[0].allocated_storage == 20 &&
      aws_db_instance.staging[0].storage_encrypted &&
      aws_db_instance.staging[0].kms_key_id == "arn:aws:kms:ap-northeast-2:000000000000:key/test" &&
      aws_db_instance.staging[0].ca_cert_identifier == "rds-ca-rsa2048-g1" &&
      aws_db_instance.staging[0].manage_master_user_password
    )
    error_message = "운영 동일 사양(MySQL 8.4.11, db.t4g.small, Multi-AZ, gp3, 암호화, 같은 CA)과 스냅샷 복원 유지 필요."
  }
  assert {
    condition = (
      aws_db_instance.staging[0].deletion_protection &&
      !aws_db_instance.staging[0].skip_final_snapshot &&
      aws_db_instance.staging[0].final_snapshot_identifier == "seonjalal-v2-staging-final" &&
      !aws_db_instance.staging[0].delete_automated_backups &&
      aws_db_instance.staging[0].backup_retention_period == 7 &&
      !aws_db_instance.staging[0].allow_major_version_upgrade &&
      !aws_db_instance.staging[0].auto_minor_version_upgrade &&
      !aws_db_instance.staging[0].apply_immediately
    )
    error_message = "삭제 보호·최종 스냅샷·백업·버전 고정·즉시 적용 금지 유지 필요."
  }
  assert {
    condition     = toset(aws_db_instance.staging[0].enabled_cloudwatch_logs_exports) == toset(["error"]) && aws_cloudwatch_log_group.mysql_error[0].name == "/aws/rds/instance/seonjalal-v2-staging/error"
    error_message = "에러 로그만 내보내고 로그 그룹 이름은 RDS 규칙과 일치해야 함."
  }
}

run "uses_own_parameter_group_not_default_or_production" {
  command = plan
  variables {
    enable_database     = true
    snapshot_identifier = "seonjalal-rehearsal-final-test"
  }
  assert {
    condition = (
      aws_db_instance.staging[0].parameter_group_name == "seonjalal-v2-staging-mysql84" &&
      aws_db_parameter_group.staging[0].family == "mysql8.4" &&
      length(aws_db_parameter_group.staging[0].parameter) == 1 &&
      one([for p in aws_db_parameter_group.staging[0].parameter : p.name]) == "log_bin_trust_function_creators" &&
      one([for p in aws_db_parameter_group.staging[0].parameter : p.value]) == "1"
    )
    error_message = "운영 그룹·기본 그룹을 쓰지 않고 log_bin_trust_function_creators=1 하나만 가진 스테이징 전용 그룹 필요."
  }
}

run "uses_new_security_group_not_stale_rehearsal_one" {
  command = plan
  variables {
    enable_database     = true
    snapshot_identifier = "seonjalal-rehearsal-final-test"
  }
  override_resource {
    target          = aws_security_group.database[0]
    override_during = plan
    values          = { id = "sg-staging-mysql" }
  }
  assert {
    condition = (
      contains(aws_db_instance.staging[0].vpc_security_group_ids, "sg-staging-mysql") &&
      !contains(aws_db_instance.staging[0].vpc_security_group_ids, "sg-05b785b7e12260ff9") &&
      aws_security_group.database[0].name == "seonjalal-v2-staging-mysql"
    )
    error_message = "리허설 잔여 규칙이 있는 기존 DB 보안 그룹을 재사용하지 않고 새 그룹만 연결해야 함."
  }
}

run "subnet_group_uses_the_two_shared_data_subnets" {
  command = plan
  variables {
    enable_database     = true
    snapshot_identifier = "seonjalal-rehearsal-final-test"
  }
  assert {
    condition     = aws_db_subnet_group.staging[0].name == "seonjalal-v2-staging" && length(aws_db_subnet_group.staging[0].subnet_ids) == 2
    error_message = "기존 공유 Data Subnet 2개를 쓰는 서브넷 그룹 필요."
  }
}

run "requires_reviewed_snapshot" {
  command = plan
  variables {
    enable_database = true
  }
  expect_failures = [aws_db_instance.staging[0]]
}

run "rejects_smaller_instance_class" {
  command = plan
  variables {
    enable_database         = true
    snapshot_identifier     = "seonjalal-rehearsal-final-test"
    database_instance_class = "db.t4g.micro"
  }
  expect_failures = [var.database_instance_class]
}

run "rejects_public_data_subnet" {
  command = plan
  variables {
    enable_database     = true
    snapshot_identifier = "seonjalal-rehearsal-final-test"
  }
  override_data {
    target = data.aws_subnet.data["subnet-data-a"]
    values = { vpc_id = "vpc-staging", availability_zone = "ap-northeast-2a", map_public_ip_on_launch = true }
  }
  expect_failures = [aws_db_subnet_group.staging[0]]
}
