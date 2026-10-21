mock_provider "aws" {}

variables { aws_account_id = "000000000000" }

run "empty_root_keeps_staging_uncreated" {
  command = plan
  assert {
    condition     = length(module.backend) == 0 && output.be_split_runtime == null
    error_message = "기본 구성에서 BE 스테이징 ECS 리소스를 만들면 안 됩니다."
  }
}
