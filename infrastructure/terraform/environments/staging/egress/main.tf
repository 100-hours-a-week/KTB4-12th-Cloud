locals {
  name_prefix = "seonjalal-v2-staging"
  required_tags = {
    Project     = "seonjalal"
    Environment = "staging"
    Version     = "v2"
    Component   = "egress"
    ManagedBy   = "terraform"
    Purpose     = "deployment-rehearsal"
  }
}
data "aws_vpc" "selected" { id = var.vpc_id }
data "aws_subnet" "nat_public" { id = var.nat_public_subnet_id }
data "aws_route_table" "nat_public" { subnet_id = var.nat_public_subnet_id }
data "aws_subnet" "app" {
  for_each = var.app_subnet_ids
  id       = each.value
}
data "aws_route_table" "app" {
  for_each  = var.app_subnet_ids
  subnet_id = each.value
}
data "aws_ssm_parameter" "nat_ami" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-6.1-x86_64"
}
resource "terraform_data" "guard" {
  lifecycle {
    precondition {
      condition     = data.aws_vpc.selected.cidr_block == "10.20.0.0/16" && data.aws_subnet.nat_public.vpc_id == var.vpc_id && data.aws_subnet.nat_public.availability_zone == "ap-northeast-2a"
      error_message = "리허설 VPC 및 Public AZ a 계약 불일치."
    }
    precondition {
      condition     = anytrue([for r in data.aws_route_table.nat_public.routes : r.cidr_block == "0.0.0.0/0" && try(startswith(r.gateway_id, "igw-"), false)])
      error_message = "Public Subnet에 기존 IGW 기본 경로 필요."
    }
    precondition {
      condition     = alltrue([for s in values(data.aws_subnet.app) : s.vpc_id == var.vpc_id && !s.map_public_ip_on_launch]) && length(toset([for s in values(data.aws_subnet.app) : s.availability_zone])) == 2
      error_message = "App Subnet은 동일 VPC의 두 AZ 및 자동 Public IP 비활성화 필요."
    }
    precondition {
      # 기존 다른 기본 경로는 덮어쓰지 않음. 자신이 만든 NAT ENI만 허용.
      condition     = alltrue(flatten([for rt in values(data.aws_route_table.app) : [for r in rt.routes : r.cidr_block != "0.0.0.0/0" || (var.enable_nat_instance && r.network_interface_id == try(aws_instance.nat[0].primary_network_interface_id, ""))]]))
      error_message = "다른 App 기본 경로 존재. 소유권 확인 전 변경 금지."
    }
  }
}
resource "aws_security_group" "nat" {
  count       = var.enable_nat_instance ? 1 : 0
  name_prefix = "${local.name_prefix}-nat-"
  description = "Temporary staging NAT; no inbound SSH"
  vpc_id      = var.vpc_id
  tags        = { Name = "${local.name_prefix}-nat" }
}
resource "aws_vpc_security_group_ingress_rule" "https" {
  for_each          = var.enable_nat_instance ? var.app_subnet_ids : {}
  security_group_id = aws_security_group.nat[0].id
  cidr_ipv4         = data.aws_subnet.app[each.key].cidr_block
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}
resource "aws_vpc_security_group_ingress_rule" "http" {
  for_each          = var.enable_nat_instance ? var.app_subnet_ids : {}
  security_group_id = aws_security_group.nat[0].id
  cidr_ipv4         = data.aws_subnet.app[each.key].cidr_block
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
}
resource "aws_vpc_security_group_egress_rule" "nat" {
  count             = var.enable_nat_instance ? 1 : 0
  security_group_id = aws_security_group.nat[0].id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}
resource "aws_iam_role" "nat" {
  count              = var.enable_nat_instance ? 1 : 0
  name               = "${local.name_prefix}-nat"
  assume_role_policy = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "ec2.amazonaws.com" }, Action = "sts:AssumeRole" }] })
}
resource "aws_iam_role_policy_attachment" "ssm" {
  count      = var.enable_nat_instance ? 1 : 0
  role       = aws_iam_role.nat[0].name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}
resource "aws_iam_instance_profile" "nat" {
  count = var.enable_nat_instance ? 1 : 0
  name  = "${local.name_prefix}-nat"
  role  = aws_iam_role.nat[0].name
}
resource "aws_instance" "nat" {
  count         = var.enable_nat_instance ? 1 : 0
  ami           = nonsensitive(data.aws_ssm_parameter.nat_ami.value)
  instance_type = "t3.micro"
  subnet_id     = var.nat_public_subnet_id
  # 최초 패키지 설치용 자동 Public IP. 이후 EIP 연결로 고정 주소 사용.
  associate_public_ip_address = true
  source_dest_check           = false
  vpc_security_group_ids      = [aws_security_group.nat[0].id]
  iam_instance_profile        = aws_iam_instance_profile.nat[0].name
  user_data                   = file("${path.module}/nat-bootstrap.sh")
  user_data_replace_on_change = true
  metadata_options {
    http_tokens   = "required"
    http_endpoint = "enabled"
  }
  root_block_device {
    volume_type = "gp3"
    volume_size = 8
    encrypted   = true
  }
  credit_specification { cpu_credits = "standard" }
  tags       = { Name = "${local.name_prefix}-nat-a" }
  depends_on = [aws_iam_role_policy_attachment.ssm, aws_vpc_security_group_egress_rule.nat]
}
resource "aws_eip" "nat" {
  count  = var.enable_nat_instance ? 1 : 0
  domain = "vpc"
  tags   = { Name = "${local.name_prefix}-nat-a" }
}
resource "aws_eip_association" "nat" {
  count         = var.enable_nat_instance ? 1 : 0
  allocation_id = aws_eip.nat[0].id
  instance_id   = aws_instance.nat[0].id
}
resource "aws_route" "app_egress" {
  for_each               = var.attach_app_routes ? var.app_subnet_ids : {}
  route_table_id         = data.aws_route_table.app[each.key].id
  destination_cidr_block = "0.0.0.0/0"
  network_interface_id   = aws_instance.nat[0].primary_network_interface_id
  depends_on             = [terraform_data.guard, aws_eip_association.nat]
}
output "network" {
  value = {
    app_subnet_ids      = var.app_subnet_ids
    app_route_table_ids = { for k, rt in data.aws_route_table.app : k => rt.id }
    nat_instance_id     = try(aws_instance.nat[0].id, null)
    nat_public_ip       = try(aws_eip.nat[0].public_ip, null)
    nat_eni_id          = try(aws_instance.nat[0].primary_network_interface_id, null)
    app_routes_attached = var.attach_app_routes
  }
}
