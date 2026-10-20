# AWS 및 실제 State에 접속하지 않는 모의 Plan 검사.
mock_provider "aws" {}
override_data {
  target = data.aws_vpc.selected
  values = { cidr_block = "10.20.0.0/16" }
}
override_data {
  target = data.aws_subnet.nat_public
  values = { vpc_id = "vpc-staging", availability_zone = "ap-northeast-2a" }
}
override_data {
  target = data.aws_route_table.nat_public
  values = { routes = [{ cidr_block = "0.0.0.0/0", gateway_id = "igw-existing" }] }
}
override_data {
  target = data.aws_subnet.app["a"]
  values = { vpc_id = "vpc-staging", availability_zone = "ap-northeast-2a", cidr_block = "10.20.10.0/24", map_public_ip_on_launch = false }
}
override_data {
  target = data.aws_subnet.app["c"]
  values = { vpc_id = "vpc-staging", availability_zone = "ap-northeast-2c", cidr_block = "10.20.11.0/24", map_public_ip_on_launch = false }
}
override_data {
  target = data.aws_route_table.app["a"]
  values = { id = "rtb-app-a", routes = [{ cidr_block = "10.20.0.0/16", gateway_id = "local" }] }
}
override_data {
  target = data.aws_route_table.app["c"]
  values = { id = "rtb-app-c", routes = [{ cidr_block = "10.20.0.0/16", gateway_id = "local" }] }
}
override_data {
  target = data.aws_ssm_parameter.nat_ami
  values = { value = "ami-0123456789abcdef0" }
}
variables {
  aws_account_id       = "000000000000"
  vpc_id               = "vpc-staging"
  nat_public_subnet_id = "subnet-public-a"
  app_subnet_ids       = { a = "subnet-app-a", c = "subnet-app-c" }
  enable_nat_instance  = false
  attach_app_routes    = false
}
run "default_has_no_nat_or_routes" {
  command = plan
  assert {
    condition     = length(aws_instance.nat) == 0 && length(aws_eip.nat) == 0 && length(aws_route.app_egress) == 0
    error_message = "기본 NAT 및 경로 비활성화 위반."
  }
}
run "prepare_nat_without_touching_app_routes" {
  command = plan
  variables { enable_nat_instance = true }
  assert {
    condition     = length(aws_instance.nat) == 1 && length(aws_eip.nat) == 1 && length(aws_route.app_egress) == 0 && !aws_instance.nat[0].source_dest_check
    error_message = "준비 단계는 NAT 1대/EIP 1개, App Route 변경 없음."
  }
  assert {
    condition     = aws_instance.nat[0].metadata_options[0].http_tokens == "required" && aws_instance.nat[0].root_block_device[0].encrypted && length(aws_vpc_security_group_ingress_rule.https) == 2
    error_message = "IMDSv2/암호화/제한된 HTTPS 허용 필요."
  }
}
run "attach_two_existing_app_routes" {
  command = plan
  variables {
    enable_nat_instance = true
    attach_app_routes   = true
  }
  assert {
    condition     = length(aws_route.app_egress) == 2 && aws_route.app_egress["a"].route_table_id == "rtb-app-a" && aws_route.app_egress["c"].route_table_id == "rtb-app-c"
    error_message = "기존 App Route Table 2개만 사용."
  }
}
run "route_without_nat_is_blocked" {
  command = plan
  variables { attach_app_routes = true }
  expect_failures = [var.attach_app_routes]
}
run "wrong_vpc_blocked" {
  command = plan
  override_data {
    target = data.aws_subnet.nat_public
    values = { vpc_id = "vpc-other", availability_zone = "ap-northeast-2a" }
  }
  expect_failures = [terraform_data.guard]
}
run "missing_igw_blocked" {
  command = plan
  override_data {
    target = data.aws_route_table.nat_public
    values = { routes = [{ cidr_block = "10.20.0.0/16", gateway_id = "local" }] }
  }
  expect_failures = [terraform_data.guard]
}
run "existing_default_route_is_not_overwritten" {
  command = plan
  override_data {
    target = data.aws_route_table.app["a"]
    values = { routes = [{ cidr_block = "0.0.0.0/0", nat_gateway_id = "nat-existing" }] }
  }
  expect_failures = [terraform_data.guard]
}
run "public_app_subnet_is_blocked" {
  command = plan
  override_data {
    target = data.aws_subnet.app["a"]
    values = { vpc_id = "vpc-staging", availability_zone = "ap-northeast-2a", map_public_ip_on_launch = true }
  }
  expect_failures = [terraform_data.guard]
}
