locals {
  name = "seonjalal-v2-staging"
}

provider "aws" {
  region              = var.aws_region
  profile             = var.aws_profile
  allowed_account_ids = [var.aws_account_id]
  default_tags {
    tags = {
      Project     = "seonjalal"
      Environment = "staging"
      Component   = "database"
      ManagedBy   = "terraform"
    }
  }
}

data "aws_subnet" "data" {
  for_each = var.data_subnet_ids
  id       = each.value
}

data "aws_route_table" "data" {
  for_each  = var.data_subnet_ids
  subnet_id = each.value
}

# 운영·AI DB와 같은 Data Subnet 2개를 쓰는 이름표. 서브넷을 새로 만들지 않으며 그룹 자체는 비용이 없다.
resource "aws_db_subnet_group" "staging" {
  count       = var.enable_database ? 1 : 0
  name        = local.name
  description = "Shared private data subnets for the staging MySQL"
  subnet_ids  = var.data_subnet_ids

  lifecycle {
    precondition {
      condition = (
        alltrue([for subnet in values(data.aws_subnet.data) :
          subnet.vpc_id == var.vpc_id && !subnet.map_public_ip_on_launch
        ]) &&
        length(toset([for subnet in values(data.aws_subnet.data) : subnet.availability_zone])) == 2
      )
      error_message = "같은 VPC의 Private Data Subnet 2개와 서로 다른 AZ 필요."
    }
    precondition {
      condition = alltrue([for table in values(data.aws_route_table.data) :
        table.vpc_id == var.vpc_id && alltrue([for route in table.routes :
          !startswith(coalesce(route.gateway_id, "none"), "igw-")
        ])
      ])
      error_message = "DB Subnet의 Route Table은 같은 VPC이며 Internet Gateway 직접 Route가 없어야 함."
    }
  }
}

# 새 보안 그룹. 리허설 DB용 그룹(sg-05b7...)은 V1 원본·DMS 허용 규칙이 남아 있어 재사용하지 않는다.
# 인바운드는 비워 둔다. 스테이징 Main/Payment Task에서 오는 3306 허용 규칙은 be_split_runtime 모듈이
# 이 그룹의 ID를 입력받아 직접 추가한다(모듈 main.tf의 database 인그레스/이그레스 규칙).
resource "aws_security_group" "database" {
  count       = var.enable_database ? 1 : 0
  name        = "${local.name}-mysql"
  description = "Staging MySQL; ingress rules are added only by the staging backend module"
  vpc_id      = var.vpc_id
}

# 운영 MySQL 그룹과 같은 설정. 운영 그룹과 공유하지 않아 스테이징 변경이 운영에 닿지 않는다.
# Flyway 마이그레이션의 함수·트리거 생성에 필요(BE 2.0.0).
resource "aws_db_parameter_group" "staging" {
  count       = var.enable_database ? 1 : 0
  name        = "${local.name}-mysql84"
  family      = "mysql8.4"
  description = "Staging MySQL 8.4; mirrors the production custom parameter"

  parameter {
    name         = "log_bin_trust_function_creators"
    value        = "1"
    apply_method = "immediate"
  }
}

# 에러 로그 보존 기간을 Terraform이 직접 관리. RDS가 먼저 자동 생성하면 보존이 무기한이 된다.
resource "aws_cloudwatch_log_group" "mysql_error" {
  count             = var.enable_database ? 1 : 0
  name              = "/aws/rds/instance/${local.name}/error"
  retention_in_days = 7
}

# 리허설 최종 스냅샷에서 복원하는 운영 동일 구성의 스테이징 MySQL.
# 복원 방식이라 username/db_name은 지정하지 않는다(스냅샷 값 승계). 마스터 비밀번호는 RDS가 새 Secret으로 관리한다.
# 가용 영역은 지정하지 않는다: Multi-AZ에서는 AZ 지정이 허용되지 않는다. 복원 후 Primary가 앱과 다른 AZ에 있으면
# 운영에서 했던 것처럼 force-failover로 맞출 수 있다(별도 승인 단계).
resource "aws_db_instance" "staging" {
  count = var.enable_database ? 1 : 0

  identifier                  = local.name
  snapshot_identifier         = var.snapshot_identifier
  engine                      = "mysql"
  engine_version              = "8.4.11"
  instance_class              = var.database_instance_class
  parameter_group_name        = aws_db_parameter_group.staging[0].name
  db_subnet_group_name        = aws_db_subnet_group.staging[0].name
  vpc_security_group_ids      = [aws_security_group.database[0].id]
  port                        = 3306
  multi_az                    = true
  publicly_accessible         = false
  storage_type                = "gp3"
  allocated_storage           = 20
  max_allocated_storage       = var.database_max_storage_gib
  storage_encrypted           = true
  kms_key_id                  = var.snapshot_kms_key_arn
  ca_cert_identifier          = "rds-ca-rsa2048-g1"
  manage_master_user_password = true

  backup_retention_period         = 7
  backup_window                   = "19:00-19:30"
  maintenance_window              = "sun:20:00-sun:21:00"
  copy_tags_to_snapshot           = true
  deletion_protection             = true
  skip_final_snapshot             = false
  final_snapshot_identifier       = "${local.name}-final"
  delete_automated_backups        = false
  auto_minor_version_upgrade      = false
  allow_major_version_upgrade     = false
  apply_immediately               = false
  monitoring_interval             = 0
  performance_insights_enabled    = false
  enabled_cloudwatch_logs_exports = ["error"]

  lifecycle {
    prevent_destroy = true
    precondition {
      condition     = var.snapshot_identifier != null
      error_message = "이 Root는 검토한 스냅샷 복원 전용이다. snapshot_identifier 지정 필요."
    }
  }

  depends_on = [aws_cloudwatch_log_group.mysql_error]
}
