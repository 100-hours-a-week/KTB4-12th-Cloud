mock_provider "aws" {}
variables {
  aws_account_id = "000000000000"
  aws_profile    = null
  deployment     = null
}

run "unset_staging_does_not_create_placeholder_runtime" {
  command = plan
  assert {
    condition     = length(module.ai) == 0 && output.ai_runtime == null
    error_message = "운영 출시 전 스테이징은 미생성 상태로 유지하고 가짜 런타임을 등록하지 않아야 함."
  }
}
