locals {
  az_keys = ["a", "c"]

  nat_gateway_az_keys = var.nat_gateway_mode == "per_az" ? local.az_keys : (
    var.nat_gateway_mode == "single" ? [local.az_keys[0]] : []
  )

  subnet_definitions = merge(
    {
      for key in local.az_keys : "public-${key}" => {
        tier = "public"
        key  = key
        cidr = var.subnet_cidrs.public[key]
        az   = var.availability_zones[index(local.az_keys, key)]
      }
    },
    {
      for key in local.az_keys : "app-${key}" => {
        tier = "app"
        key  = key
        cidr = var.subnet_cidrs.app[key]
        az   = var.availability_zones[index(local.az_keys, key)]
      }
    },
    {
      for key in local.az_keys : "data-${key}" => {
        tier = "data"
        key  = key
        cidr = var.subnet_cidrs.data[key]
        az   = var.availability_zones[index(local.az_keys, key)]
      }
    },
  )

  # 모든 Internet Default Route를 이 Map에서만 생성해 Data Tier가 실수로 포함되는지 Plan에서 검사한다.
  default_routes = {
    public = {
      tier           = "public"
      route_table_id = aws_route_table.public.id
      gateway_id     = aws_internet_gateway.this.id
    }
  }
}

resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = {
    Name = "${var.name_prefix}-vpc"
  }
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id

  tags = {
    Name = "${var.name_prefix}-igw"
  }
}

resource "aws_subnet" "this" {
  for_each = local.subnet_definitions

  vpc_id                  = aws_vpc.this.id
  availability_zone       = each.value.az
  cidr_block              = each.value.cidr
  map_public_ip_on_launch = false

  tags = {
    Name = "${var.name_prefix}-${each.key}"
    Tier = each.value.tier
  }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id

  tags = {
    Name = "${var.name_prefix}-public-rt"
    Tier = "public"
  }
}

resource "aws_route_table_association" "public" {
  for_each = toset(local.az_keys)

  subnet_id      = aws_subnet.this["public-${each.key}"].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table" "app" {
  for_each = toset(local.az_keys)

  vpc_id = aws_vpc.this.id

  tags = {
    Name = "${var.name_prefix}-app-${each.key}-rt"
    Tier = "app"
  }
}

resource "aws_eip" "nat" {
  for_each = toset(local.nat_gateway_az_keys)

  domain = "vpc"

  tags = {
    Name = "${var.name_prefix}-nat-${each.key}-eip"
  }
}

resource "aws_nat_gateway" "this" {
  for_each = toset(local.nat_gateway_az_keys)

  allocation_id = aws_eip.nat[each.key].id
  subnet_id     = aws_subnet.this["public-${each.key}"].id

  tags = {
    Name = "${var.name_prefix}-nat-${each.key}"
  }

  depends_on = [aws_internet_gateway.this]
}

resource "aws_route" "app_default" {
  for_each = var.nat_gateway_mode == "none" ? toset([]) : toset(local.az_keys)

  route_table_id         = aws_route_table.app[each.key].id
  destination_cidr_block = "0.0.0.0/0"
  nat_gateway_id = aws_nat_gateway.this[
    var.nat_gateway_mode == "per_az" ? each.key : local.az_keys[0]
  ].id
}

resource "aws_route" "default" {
  for_each = local.default_routes

  route_table_id         = each.value.route_table_id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = each.value.gateway_id
}

resource "aws_route_table_association" "app" {
  for_each = toset(local.az_keys)

  subnet_id      = aws_subnet.this["app-${each.key}"].id
  route_table_id = aws_route_table.app[each.key].id
}

# Data Route Table에는 의도적으로 Internet/NAT Default Route를 만들지 않는다.
resource "aws_route_table" "data" {
  for_each = toset(local.az_keys)

  vpc_id = aws_vpc.this.id

  tags = {
    Name = "${var.name_prefix}-data-${each.key}-rt"
    Tier = "data"
  }
}

resource "aws_route_table_association" "data" {
  for_each = toset(local.az_keys)

  subnet_id      = aws_subnet.this["data-${each.key}"].id
  route_table_id = aws_route_table.data[each.key].id
}

resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.this.id
  service_name      = "com.amazonaws.${data.aws_region.current.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [for route_table in aws_route_table.app : route_table.id]

  tags = {
    Name = "${var.name_prefix}-s3-endpoint"
  }
}

data "aws_region" "current" {}

resource "aws_security_group" "alb" {
  name_prefix = "${var.name_prefix}-alb-"
  description = "Internet HTTPS entry point for V2 ALB"
  vpc_id      = aws_vpc.this.id

  tags = {
    Name = "${var.name_prefix}-alb-sg"
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_security_group_ingress_rule" "alb_http" {
  security_group_id = aws_security_group.alb.id
  description       = "HTTP redirect entry point"
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 80
  to_port           = 80
  ip_protocol       = "tcp"
}

resource "aws_vpc_security_group_ingress_rule" "alb_https" {
  security_group_id = aws_security_group.alb.id
  description       = "Public HTTPS entry point"
  cidr_ipv4         = "0.0.0.0/0"
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
}

resource "aws_security_group" "ecs" {
  name_prefix = "${var.name_prefix}-ecs-"
  description = "V2 ECS task network boundary"
  vpc_id      = aws_vpc.this.id

  tags = {
    Name = "${var.name_prefix}-ecs-sg"
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_security_group_ingress_rule" "ecs_from_alb" {
  security_group_id            = aws_security_group.ecs.id
  description                  = "Backend traffic from the ALB only"
  referenced_security_group_id = aws_security_group.alb.id
  from_port                    = var.application_port
  to_port                      = var.application_port
  ip_protocol                  = "tcp"
}

resource "aws_vpc_security_group_egress_rule" "alb_to_ecs" {
  security_group_id            = aws_security_group.alb.id
  description                  = "Forward requests to ECS tasks"
  referenced_security_group_id = aws_security_group.ecs.id
  from_port                    = var.application_port
  to_port                      = var.application_port
  ip_protocol                  = "tcp"
}

# ECR, Logs, Secrets와 외부 AI API 경로가 확정되기 전 A3의 임시 Egress다. A4에서 목적별로 재검토한다.
resource "aws_vpc_security_group_egress_rule" "ecs_all" {
  security_group_id = aws_security_group.ecs.id
  description       = "Temporary A3 egress; review and narrow in A4"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

resource "aws_security_group" "rds" {
  name_prefix = "${var.name_prefix}-rds-"
  description = "V2 RDS network boundary"
  vpc_id      = aws_vpc.this.id

  tags = {
    Name = "${var.name_prefix}-rds-sg"
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_vpc_security_group_ingress_rule" "rds_from_ecs" {
  security_group_id            = aws_security_group.rds.id
  description                  = "MySQL from ECS tasks only"
  referenced_security_group_id = aws_security_group.ecs.id
  from_port                    = 3306
  to_port                      = 3306
  ip_protocol                  = "tcp"
}

check "subnet_cidrs_are_unique" {
  assert {
    condition = length(distinct(concat(
      values(var.subnet_cidrs.public),
      values(var.subnet_cidrs.app),
      values(var.subnet_cidrs.data),
    ))) == 6
    error_message = "Public, App, Data Subnet CIDR 여섯 개는 서로 달라야 합니다."
  }
}

check "data_route_tables_have_no_default_route" {
  assert {
    condition = alltrue([
      for route in local.default_routes : route.tier != "data"
    ])
    error_message = "Default Route 선언에는 Data Tier를 포함할 수 없습니다."
  }
}

check "nat_gateway_mode_is_valid" {
  assert {
    condition     = contains(["none", "single", "per_az"], var.nat_gateway_mode)
    error_message = "nat_gateway_mode는 none, single, per_az 중 하나여야 합니다."
  }
}
