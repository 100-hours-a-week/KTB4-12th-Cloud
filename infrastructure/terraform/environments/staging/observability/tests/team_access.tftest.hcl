mock_provider "aws" {
  mock_data "aws_subnet" {
    defaults = {
      vpc_id                     = "vpc-058d65b38801b9951"
      cidr_block                 = "10.20.10.0/24"
      available_ip_address_count = 50
    }
  }
  mock_data "aws_ssm_parameter" {
    defaults = { value = "ami-0123456789abcdef0" }
  }
}

override_data {
  target = data.aws_security_group.host_metrics["v1_benchmark"]
  values = { vpc_id = "vpc-0dcb5284ffb90203a" }
}
override_data {
  target = data.aws_security_group.host_metrics["v2_staging"]
  values = { vpc_id = "vpc-058d65b38801b9951" }
}
override_data {
  target = data.aws_security_group.host_metrics["v2_develop"]
  values = { vpc_id = "vpc-058d65b38801b9951" }
}

run "grafana_is_reachable_only_from_the_router_on_3000" {
  command = plan
  assert {
    condition = (
      aws_vpc_security_group_ingress_rule.grafana_from_team_router.from_port == 3000 &&
      aws_vpc_security_group_ingress_rule.grafana_from_team_router.to_port == 3000 &&
      aws_vpc_security_group_ingress_rule.grafana_from_team_router.ip_protocol == "tcp" &&
      aws_vpc_security_group_ingress_rule.grafana_from_team_router.cidr_ipv4 == "10.20.0.28/32"
    )
    error_message = "Grafana는 Tailscale 라우터 IP 하나에서 3000/tcp로만 열려야 한다."
  }
}

run "rule_has_no_ipv6_or_group_source" {
  command = plan
  assert {
    condition = (
      aws_vpc_security_group_ingress_rule.grafana_from_team_router.cidr_ipv6 == null &&
      aws_vpc_security_group_ingress_rule.grafana_from_team_router.referenced_security_group_id == null
    )
    error_message = "출발지는 IPv4 /32 하나여야 한다."
  }
}

run "public_cidr_is_rejected" {
  command = plan
  variables {
    grafana_access_cidr = "0.0.0.0/0"
  }
  expect_failures = [var.grafana_access_cidr]
}

run "whole_subnet_is_rejected" {
  command = plan
  variables {
    grafana_access_cidr = "10.20.0.0/24"
  }
  expect_failures = [var.grafana_access_cidr]
}

run "other_private_ranges_are_rejected" {
  command = plan
  variables {
    grafana_access_cidr = "172.31.6.212/32"
  }
  expect_failures = [var.grafana_access_cidr]
}

run "malformed_address_is_rejected" {
  command = plan
  variables {
    grafana_access_cidr = "10.20.999.1/32"
  }
  expect_failures = [var.grafana_access_cidr]
}

run "monitor_egress_is_not_widened" {
  command = plan
  assert {
    condition = (
      aws_vpc_security_group_egress_rule.https.from_port == 443 &&
      aws_vpc_security_group_egress_rule.https.to_port == 443
    )
    error_message = "아웃바운드 HTTPS 규칙은 바뀌면 안 된다."
  }
}
