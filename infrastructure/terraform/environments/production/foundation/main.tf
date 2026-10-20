# 1단계: 운영 전용 이름과 권한 경계만 준비. DB/호스트/앱/트래픽은 생성하지 않음.
provider "aws" {
  region              = var.aws_region
  profile             = var.aws_profile
  allowed_account_ids = [var.aws_account_id]
  default_tags {
    tags = { Project = "seonjalal", Environment = "production", ManagedBy = "terraform" }
  }
}
data "aws_subnet" "data" {
  for_each = var.data_subnet_ids
  id       = each.value
}
resource "terraform_data" "network_guard" {
  lifecycle {
    precondition {
      condition = alltrue([for subnet in values(data.aws_subnet.data) :
        subnet.vpc_id == var.vpc_id && !subnet.map_public_ip_on_launch
      ]) && length(toset([for subnet in values(data.aws_subnet.data) : subnet.availability_zone])) == 2
      error_message = "Data Subnet은 선택한 VPC 내 서로 다른 두 AZ이며 공인 IP 자동 할당이 꺼져 있어야 함. 격리 Route는 별도 검증 필요."
    }
  }
}
resource "aws_ecs_cluster" "production" {
  name = "seonjalal-v2-production-cluster"
  setting {
    name  = "containerInsights"
    value = "disabled"
  }
  lifecycle { prevent_destroy = true }
}
resource "aws_ecr_repository" "backend" {
  name                 = "seonjalal/production/backend"
  image_tag_mutability = "IMMUTABLE"
  force_delete         = false
  image_scanning_configuration { scan_on_push = true }
  lifecycle { prevent_destroy = true }
}
# Secret 원문은 Terraform에 입력하지 않음. 운영 인증키 보존/주입은 별도 절차.
resource "aws_secretsmanager_secret" "runtime" {
  name                    = "seonjalal/production/backend/runtime"
  recovery_window_in_days = 30
  lifecycle { prevent_destroy = true }
}
resource "aws_cloudwatch_log_group" "backend" {
  name              = "/ecs/seonjalal-v2-production/backend"
  retention_in_days = 30
  lifecycle { prevent_destroy = true }
}
# 외부 접근 및 다른 환경 SG 참조 없이 운영 그룹끼리만 최소 연결.
resource "aws_security_group" "alb" {
  name        = "seonjalal-v2-production-alb"
  description = "Production ALB boundary; public ingress configured separately"
  vpc_id      = var.vpc_id
}
resource "aws_security_group" "backend" {
  name        = "seonjalal-v2-production-backend"
  description = "Production task boundary; external egress configured separately"
  vpc_id      = var.vpc_id
}
resource "aws_security_group" "database" {
  name        = "seonjalal-v2-production-rds"
  description = "Production database boundary; migration source configured separately"
  vpc_id      = var.vpc_id
}
resource "aws_vpc_security_group_egress_rule" "alb_to_backend" {
  security_group_id            = aws_security_group.alb.id
  referenced_security_group_id = aws_security_group.backend.id
  ip_protocol                  = "tcp"
  from_port                    = 8080
  to_port                      = 8080
}
resource "aws_vpc_security_group_ingress_rule" "backend_from_alb" {
  security_group_id            = aws_security_group.backend.id
  referenced_security_group_id = aws_security_group.alb.id
  ip_protocol                  = "tcp"
  from_port                    = 8080
  to_port                      = 8080
}
resource "aws_vpc_security_group_egress_rule" "backend_to_database" {
  security_group_id            = aws_security_group.backend.id
  referenced_security_group_id = aws_security_group.database.id
  ip_protocol                  = "tcp"
  from_port                    = 3306
  to_port                      = 3306
}
resource "aws_vpc_security_group_ingress_rule" "database_from_backend" {
  security_group_id            = aws_security_group.database.id
  referenced_security_group_id = aws_security_group.backend.id
  ip_protocol                  = "tcp"
  from_port                    = 3306
  to_port                      = 3306
}
resource "aws_db_subnet_group" "production" {
  name       = "seonjalal-v2-production"
  subnet_ids = var.data_subnet_ids
  depends_on = [terraform_data.network_guard]
  lifecycle { prevent_destroy = true }
}
