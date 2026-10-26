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

# 호스트 지표 규칙의 VPC 검증을 통과시키기 위한 대상별 VPC(V1 벤치마크만 다른 VPC).
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

run "defaults_to_t3_medium" {
  command = plan
  assert {
    condition     = aws_instance.monitor.instance_type == "t3.medium"
    error_message = "모니터링 서버 기본 유형은 t3.medium이어야 한다."
  }
}

run "t3_large_is_allowed" {
  command = plan
  variables {
    monitor_instance_type = "t3.large"
  }
  assert {
    condition     = aws_instance.monitor.instance_type == "t3.large"
    error_message = "t3.large로 올릴 수 있어야 한다."
  }
}

run "t3_small_is_rejected" {
  command = plan
  variables {
    monitor_instance_type = "t3.small"
  }
  expect_failures = [var.monitor_instance_type]
}

run "other_families_are_rejected" {
  command = plan
  variables {
    monitor_instance_type = "m5.large"
  }
  expect_failures = [var.monitor_instance_type]
}

run "size_change_keeps_the_safety_settings" {
  command = plan
  assert {
    condition = (
      aws_instance.monitor.associate_public_ip_address == false &&
      aws_instance.monitor.metadata_options[0].http_tokens == "required" &&
      aws_instance.monitor.credit_specification[0].cpu_credits == "standard" &&
      one(aws_instance.monitor.root_block_device).encrypted == true &&
      one(aws_instance.monitor.root_block_device).volume_size == 30 &&
      one(aws_instance.monitor.root_block_device).volume_type == "gp3"
    )
    error_message = "유형 변경이 공개 IP 없음, IMDSv2, 암호화·30GiB 볼륨, 크레딧 설정을 바꾸면 안 된다."
  }
}

run "network_and_subnet_are_unchanged" {
  command = plan
  assert {
    condition = (
      aws_instance.monitor.subnet_id == "subnet-08c2017235a4a56da" &&
      length(aws_instance.monitor.vpc_security_group_ids) == 1
    )
    error_message = "서브넷과 보안 그룹 연결은 변경되면 안 된다."
  }
}
