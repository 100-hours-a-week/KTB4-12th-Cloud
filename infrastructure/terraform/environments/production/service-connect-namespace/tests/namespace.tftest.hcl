mock_provider "aws" {}

variables {
  aws_account_id = "721744297924"
  aws_profile    = null
}

run "production_namespace_is_isolated" {
  command = plan
  assert {
    condition = (
      aws_service_discovery_http_namespace.production.name == "seonjalal-v2-production-services" &&
      output.namespace_name == "seonjalal-v2-production-services"
    )
    error_message = "운영 BE·AI 공용 Service Connect Namespace 이름은 고정돼야 합니다."
  }
}

run "wrong_account_is_rejected" {
  command = plan
  variables {
    aws_account_id = "000000000000"
  }
  expect_failures = [var.aws_account_id]
}
