locals {
  name = "seonjalal-v2-production-ai-postgres"
}

provider "aws" {
  region              = var.aws_region
  profile             = var.aws_profile
  allowed_account_ids = [var.aws_account_id]
  default_tags {
    tags = {
      Project     = "seonjalal"
      Environment = "production"
      Component   = "ai-database"
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

resource "aws_db_subnet_group" "ai" {
  name        = local.name
  description = "Existing private subnets; production AI DB stays in Seoul A"
  subnet_ids  = var.data_subnet_ids

  lifecycle {
    precondition {
      condition = (
        alltrue([for subnet in values(data.aws_subnet.data) :
          subnet.vpc_id == var.vpc_id && !subnet.map_public_ip_on_launch
        ]) &&
        length(toset([for subnet in values(data.aws_subnet.data) : subnet.availability_zone])) == 2 &&
        contains([for subnet in values(data.aws_subnet.data) : subnet.availability_zone], "ap-northeast-2a")
      )
      error_message = "같은 VPC의 Private Data Subnet 2개와 서로 다른 AZ 필요. 서울 A 포함 필수."
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

resource "aws_security_group" "database" {
  name        = local.name
  description = "Private production AI PostgreSQL; accept only AI database client security group"
  vpc_id      = var.vpc_id
}

# AI 앱과 일회성 Migration Task에만 연결할 접근 Identity. BE에는 연결하지 않는다.
resource "aws_security_group" "client" {
  name        = "${local.name}-client"
  description = "Attach only to production AI application and migration tasks"
  vpc_id      = var.vpc_id
}

resource "aws_vpc_security_group_ingress_rule" "database_from_ai" {
  security_group_id            = aws_security_group.database.id
  referenced_security_group_id = aws_security_group.client.id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
}

resource "aws_vpc_security_group_egress_rule" "ai_to_database" {
  security_group_id            = aws_security_group.client.id
  referenced_security_group_id = aws_security_group.database.id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
}

resource "aws_db_parameter_group" "ai" {
  name        = local.name
  family      = "postgres18"
  description = "AI production PostgreSQL TLS enforcement"
  parameter {
    name         = "rds.force_ssl"
    value        = "1"
    apply_method = "immediate"
  }
}

resource "aws_cloudwatch_log_group" "postgresql" {
  name              = "/aws/rds/instance/${local.name}/postgresql"
  retention_in_days = 30
}

resource "aws_db_instance" "ai" {
  identifier                  = local.name
  engine                      = "postgres"
  engine_version              = "18.6"
  instance_class              = var.database_instance_class
  db_name                     = "ai_db_production"
  username                    = "ai_admin"
  manage_master_user_password = true
  # 비밀번호는 Terraform State에 입력하지 않고 RDS 관리형 Secret에 맡긴다.
  parameter_group_name            = aws_db_parameter_group.ai.name
  db_subnet_group_name            = aws_db_subnet_group.ai.name
  vpc_security_group_ids          = [aws_security_group.database.id]
  port                            = 5432
  availability_zone               = "ap-northeast-2a"
  multi_az                        = false
  publicly_accessible             = false
  storage_type                    = "gp3"
  allocated_storage               = 20
  max_allocated_storage           = 0
  storage_encrypted               = true
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
  enabled_cloudwatch_logs_exports = ["postgresql"]

  lifecycle {
    prevent_destroy = true
  }

  depends_on = [
    aws_vpc_security_group_ingress_rule.database_from_ai,
    aws_vpc_security_group_egress_rule.ai_to_database,
    aws_cloudwatch_log_group.postgresql,
  ]
}
