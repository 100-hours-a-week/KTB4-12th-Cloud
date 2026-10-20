# 실제 AWS 호출 없이 전용 경로와 운영 보호 조건 검증.
mock_provider "aws" {
  mock_data "aws_subnet" {
    defaults = { vpc_id = "vpc-staging", map_public_ip_on_launch = false }
  }
  mock_data "aws_route_table" {
    defaults = { vpc_id = "vpc-staging" }
  }
}
override_data {
  target = data.aws_subnet.app["subnet-app-a"]
  values = { vpc_id = "vpc-staging", cidr_block = "10.20.10.0/24", availability_zone = "ap-northeast-2a", map_public_ip_on_launch = false }
}
override_data {
  target = data.aws_subnet.app["subnet-app-c"]
  values = { vpc_id = "vpc-staging", cidr_block = "10.20.11.0/24", availability_zone = "ap-northeast-2c", map_public_ip_on_launch = false }
}
override_data {
  target = data.aws_subnet.public["subnet-public-a"]
  values = { vpc_id = "vpc-staging", availability_zone = "ap-northeast-2a" }
}
override_data {
  target = data.aws_subnet.public["subnet-public-c"]
  values = { vpc_id = "vpc-staging", availability_zone = "ap-northeast-2c" }
}
override_data {
  target = data.aws_instance.tailscale_router[0]
  values = { subnet_id = "subnet-router", instance_state = "running", private_ip = "10.20.0.28" }
}
override_data {
  target = data.aws_instance.cutover_source["rehearsal"]
  values = { subnet_id = "subnet-source", instance_state = "running", private_ip = "172.31.6.212" }
}
override_data {
  target = data.aws_instance.cutover_production_guard["rehearsal"]
  values = { subnet_id = "subnet-production" }
}
override_data {
  target = data.aws_instances.cutover_subnet_occupants["rehearsal"]
  values = { ids = ["i-source"] }
}
override_data {
  target = data.aws_subnet.cutover_source["rehearsal"]
  values = { vpc_id = "vpc-source" }
}
override_data {
  target = data.aws_vpc_peering_connection.cutover["rehearsal"]
  values = { status = "active", vpc_id = "vpc-source", peer_vpc_id = "vpc-staging" }
}
override_data {
  target = data.aws_route_table.cutover_baseline["rehearsal"]
  values = {
    vpc_id = "vpc-source"
    routes = [
      { cidr_block = "0.0.0.0/0", gateway_id = "igw-test" },
      { cidr_block = "10.20.20.0/24", vpc_peering_connection_id = "pcx-test" },
      { cidr_block = "10.20.21.0/24", vpc_peering_connection_id = "pcx-test" }
    ]
  }
}
variables {
  v1_candidate_secret_role_name      = null
  aws_account_id                     = "000000000000"
  vpc_id                             = "vpc-staging"
  app_subnet_ids                     = ["subnet-app-a", "subnet-app-c"]
  public_subnet_ids                  = ["subnet-public-a", "subnet-public-c"]
  secrets_endpoint_security_group_id = "sg-0123456789abcdef0"
  database_security_group_id         = "sg-0123456789abcdef1"
  product_image_bucket_arn           = "arn:aws:s3:::staging-example"
  asg_desired_capacity               = 0
  asg_min_size                       = 0
  enable_alb                         = true
  alb_certificate_arn                = "arn:aws:acm:ap-northeast-2:000000000000:certificate/test"
  tailscale_router_instance_id       = "i-0123456789abcdef0"
  enable_staging_certificate         = false
  enable_service                     = false
  enable_runtime_probe               = false
  container_secrets                  = {}
  database_fixture_verified          = true
  v1_cutover_network = {
    source_instance_id      = "i-source"
    production_instance_id  = "i-production"
    source_subnet_id        = "subnet-source"
    source_vpc_id           = "vpc-source"
    source_private_ip       = "172.31.6.212"
    baseline_route_table_id = "rtb-baseline"
    peering_id              = "pcx-test"
  }
}
run "isolated_routes_preserve_internet_and_data" {
  command = plan
  assert {
    condition = (
      length(aws_route_table.v1_cutover) == 1 &&
      aws_route_table_association.v1_cutover["rehearsal"].subnet_id == "subnet-source" &&
      aws_route.v1_cutover_internet["rehearsal"].gateway_id == "igw-test" &&
      length(aws_route.v1_cutover_peering) == 4 &&
      length(aws_route.v1_cutover_return) == 2 &&
      aws_vpc_security_group_ingress_rule.alb_from_v1_cutover["rehearsal"].cidr_ipv4 == "172.31.6.212/32" &&
      aws_vpc_security_group_ingress_rule.alb_from_v1_cutover["rehearsal"].to_port == 443
    )
    error_message = "전용 서브넷 연결·인터넷/Data 보존·App 두 응답 경로·단일 출발지 HTTPS 허용 필요."
  }
}
run "other_subnet_occupant_blocks_changes" {
  command = plan
  override_data {
    target = data.aws_instances.cutover_subnet_occupants["rehearsal"]
    values = { ids = ["i-source", "i-other"] }
  }
  expect_failures = [aws_route_table.v1_cutover]
}
run "production_subnet_blocks_changes" {
  command = plan
  override_data {
    target = data.aws_instance.cutover_production_guard["rehearsal"]
    values = { subnet_id = "subnet-source" }
  }
  expect_failures = [aws_route_table.v1_cutover]
}
run "inactive_peer_blocks_changes" {
  command = plan
  override_data {
    target = data.aws_vpc_peering_connection.cutover["rehearsal"]
    values = { status = "pending-acceptance", vpc_id = "vpc-source", peer_vpc_id = "vpc-staging" }
  }
  expect_failures = [aws_route_table.v1_cutover]
}
run "unexpected_baseline_routes_block_changes" {
  command = plan
  override_data {
    target = data.aws_route_table.cutover_baseline["rehearsal"]
    values = {
      vpc_id = "vpc-source"
      routes = [
        { cidr_block = "0.0.0.0/0", gateway_id = "igw-test" },
        { cidr_block = "10.20.20.0/24", vpc_peering_connection_id = "pcx-test" },
        { cidr_block = "10.20.21.0/24", vpc_peering_connection_id = "pcx-test" },
        { cidr_block = "192.168.0.0/16", gateway_id = "vgw-test" }
      ]
    }
  }
  expect_failures = [aws_route_table.v1_cutover]
}
run "non_fixture_database_blocks_network" {
  command = plan
  variables { database_fixture_verified = false }
  expect_failures = [var.v1_cutover_network]
}
run "candidate_secret_access_is_disabled_by_default" {
  command = plan
  assert {
    condition     = length(aws_iam_role_policy.v1_candidate_runtime) == 0
    error_message = "명시적으로 선택하지 않은 후보 Role에 Secret 접근 권한 추가 금지."
  }
}
run "candidate_can_read_only_current_fixture_secret" {
  command = plan
  variables { v1_candidate_secret_role_name = "seonjalal-v1-develop-role" }
  override_resource {
    target          = aws_secretsmanager_secret.backend_runtime
    override_during = plan
    values          = { arn = "arn:aws:secretsmanager:ap-northeast-2:000000000000:secret:fixture-test" }
  }
  assert {
    condition = (
      aws_iam_role_policy.v1_candidate_runtime[0].role == "seonjalal-v1-develop-role" &&
      length(jsondecode(aws_iam_role_policy.v1_candidate_runtime[0].policy).Statement) == 1 &&
      jsondecode(aws_iam_role_policy.v1_candidate_runtime[0].policy).Statement[0].Action == ["secretsmanager:GetSecretValue"] &&
      jsondecode(aws_iam_role_policy.v1_candidate_runtime[0].policy).Statement[0].Resource == "arn:aws:secretsmanager:ap-northeast-2:000000000000:secret:fixture-test" &&
      jsondecode(aws_iam_role_policy.v1_candidate_runtime[0].policy).Statement[0].Condition.StringEquals["secretsmanager:VersionStage"] == "AWSCURRENT"
    )
    error_message = "해당 시험 Secret의 현재 버전 조회만 허용하고 쓰기·전체 Secret·KMS 광역 권한 추가 금지."
  }
}
run "candidate_cannot_grant_production_role" {
  command = plan
  variables { v1_candidate_secret_role_name = "seonjalal-v1-ec2-role" }
  expect_failures = [var.v1_candidate_secret_role_name]
}
