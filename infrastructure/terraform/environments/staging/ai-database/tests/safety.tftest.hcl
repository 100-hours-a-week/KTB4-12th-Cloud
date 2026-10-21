# 실제 AWS/SQL 호출 없이 격리·데이터 보존·최소 사양 계약 검증.
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

run "private_single_az_database_preserves_data_and_secret_boundary" {
  command = plan
  assert {
    condition = (
      aws_db_instance.ai.engine == "postgres" &&
      aws_db_instance.ai.engine_version == "18.6" &&
      aws_db_instance.ai.db_name == "ai_db_staging" &&
      aws_db_instance.ai.availability_zone == "ap-northeast-2a" &&
      !aws_db_instance.ai.multi_az && !aws_db_instance.ai.publicly_accessible &&
      aws_db_instance.ai.port == 5432 &&
      aws_db_instance.ai.instance_class == "db.t4g.micro" &&
      aws_db_instance.ai.allocated_storage == 20 &&
      aws_db_instance.ai.max_allocated_storage == 0 &&
      aws_db_instance.ai.storage_type == "gp3" &&
      aws_db_instance.ai.storage_encrypted &&
      aws_db_instance.ai.manage_master_user_password &&
      aws_db_instance.ai.username != "ai_migrator" &&
      aws_db_instance.ai.deletion_protection &&
      !aws_db_instance.ai.skip_final_snapshot &&
      !aws_db_instance.ai.delete_automated_backups &&
      aws_db_instance.ai.backup_retention_period == 7 &&
      !aws_db_instance.ai.allow_major_version_upgrade &&
      !aws_db_instance.ai.auto_minor_version_upgrade
    )
    error_message = "스테이징 단일 AZ·Private·암호화·별도 관리자 Secret·백업/삭제 보호·고정 용량/버전 유지 필요."
  }
  assert {
    condition = anytrue([for parameter in aws_db_parameter_group.ai.parameter :
      parameter.name == "rds.force_ssl" && parameter.value == "1"
    ]) && aws_db_parameter_group.ai.family == "postgres18"
    error_message = "PostgreSQL18 및 서버 측 TLS 강제 필요. 클라이언트 인증서 검증은 별도 필요."
  }
}

run "database_accepts_only_ai_client_on_postgres_port" {
  command = plan
  # SG ID를 Plan 단계에서 고정해 실제 정책의 참조 대상을 비교한다.
  override_resource {
    target          = aws_security_group.database
    override_during = plan
    values          = { id = "sg-ai-database" }
  }
  override_resource {
    target          = aws_security_group.client
    override_during = plan
    values          = { id = "sg-ai-client" }
  }
  assert {
    condition = (
      aws_vpc_security_group_ingress_rule.database_from_ai.security_group_id == "sg-ai-database" &&
      aws_vpc_security_group_ingress_rule.database_from_ai.referenced_security_group_id == "sg-ai-client" &&
      aws_vpc_security_group_ingress_rule.database_from_ai.from_port == 5432 &&
      aws_vpc_security_group_ingress_rule.database_from_ai.to_port == 5432 &&
      aws_vpc_security_group_ingress_rule.database_from_ai.ip_protocol == "tcp" &&
      aws_vpc_security_group_ingress_rule.database_from_ai.cidr_ipv4 == null &&
      aws_vpc_security_group_ingress_rule.database_from_ai.cidr_ipv6 == null &&
      aws_vpc_security_group_egress_rule.ai_to_database.security_group_id == "sg-ai-client" &&
      aws_vpc_security_group_egress_rule.ai_to_database.referenced_security_group_id == "sg-ai-database" &&
      aws_vpc_security_group_egress_rule.ai_to_database.from_port == 5432 &&
      aws_vpc_security_group_egress_rule.ai_to_database.to_port == 5432 &&
      toset(aws_db_instance.ai.vpc_security_group_ids) == toset(["sg-ai-database"])
    )
    error_message = "AI Client SG에서 PostgreSQL5432만 허용. 광역 CIDR·BE/운영 SG 접근 금지."
  }
}

run "same_az_subnet_group_is_rejected" {
  command = plan
  override_data {
    target = data.aws_subnet.data["subnet-data-c"]
    values = { vpc_id = "vpc-staging", availability_zone = "ap-northeast-2a", map_public_ip_on_launch = false }
  }
  expect_failures = [aws_db_subnet_group.ai]
}

run "wrong_vpc_subnet_is_rejected" {
  command = plan
  override_data {
    target = data.aws_subnet.data["subnet-data-c"]
    values = { vpc_id = "vpc-other", availability_zone = "ap-northeast-2c", map_public_ip_on_launch = false }
  }
  expect_failures = [aws_db_subnet_group.ai]
}

run "public_subnet_is_rejected" {
  command = plan
  override_data {
    target = data.aws_subnet.data["subnet-data-c"]
    values = { vpc_id = "vpc-staging", availability_zone = "ap-northeast-2c", map_public_ip_on_launch = true }
  }
  expect_failures = [aws_db_subnet_group.ai]
}

run "internet_gateway_route_is_rejected" {
  command = plan
  override_data {
    target = data.aws_route_table.data["subnet-data-c"]
    values = { vpc_id = "vpc-staging", routes = [{ gateway_id = "igw-public", cidr_block = "0.0.0.0/0" }] }
  }
  expect_failures = [aws_db_subnet_group.ai]
}

run "missing_seoul_a_is_rejected" {
  command = plan
  override_data {
    target = data.aws_subnet.data["subnet-data-a"]
    values = { vpc_id = "vpc-staging", availability_zone = "ap-northeast-2b", map_public_ip_on_launch = false }
  }
  expect_failures = [aws_db_subnet_group.ai]
}

run "unreviewed_instance_cost_is_rejected" {
  command = plan
  variables { database_instance_class = "db.r7g.16xlarge" }
  expect_failures = [var.database_instance_class]
}

run "other_region_is_rejected" {
  command = plan
  variables { aws_region = "us-east-1" }
  expect_failures = [var.aws_region]
}
