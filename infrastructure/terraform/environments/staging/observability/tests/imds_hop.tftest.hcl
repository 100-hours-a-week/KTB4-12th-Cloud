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

run "containers_can_reach_imds_through_the_docker_bridge" {
  command = plan
  assert {
    condition     = aws_instance.monitor.metadata_options[0].http_put_response_hop_limit == 2
    error_message = "Grafana 컨테이너가 IMDS에 닿으려면 홉 제한이 2여야 한다."
  }
}

run "imds_stays_hardened" {
  command = plan
  assert {
    condition = (
      aws_instance.monitor.metadata_options[0].http_endpoint == "enabled" &&
      aws_instance.monitor.metadata_options[0].http_tokens == "required"
    )
    error_message = "홉 제한을 올려도 IMDSv2 필수는 유지되어야 한다."
  }
}

run "cloudwatch_role_stays_metrics_read_only" {
  command = plan
  assert {
    condition = (
      length(jsondecode(aws_iam_role_policy.cloudwatch_metrics.policy).Statement) == 1 &&
      toset(jsondecode(aws_iam_role_policy.cloudwatch_metrics.policy).Statement[0].Action) == toset([
        "cloudwatch:GetMetricData", "cloudwatch:GetMetricStatistics", "cloudwatch:ListMetrics"
      ])
    )
    error_message = "컨테이너가 쓰게 되는 역할은 지표 조회 권한만 가져야 한다."
  }
}
