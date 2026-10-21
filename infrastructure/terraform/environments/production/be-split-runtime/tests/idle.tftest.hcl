mock_provider "aws" {}

variables {
  aws_account_id = "000000000000"
  deployment     = null
}

run "empty_root_keeps_production_uncreated" {
  command = plan
  assert {
    condition     = length(module.backend) == 0 && output.be_split_runtime == null
    error_message = "실제 입력 전에는 운영 BE 분리 ECS 리소스를 만들면 안 됩니다."
  }
}
