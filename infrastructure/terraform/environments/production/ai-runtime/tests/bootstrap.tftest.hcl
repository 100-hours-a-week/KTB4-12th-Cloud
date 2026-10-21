mock_provider "aws" {
  mock_data "aws_subnet" {
    defaults = { vpc_id = "vpc-production", availability_zone = "ap-northeast-2a", map_public_ip_on_launch = false }
  }
  mock_data "aws_security_group" {
    defaults = { vpc_id = "vpc-production" }
  }
  mock_data "aws_vpc_endpoint" {
    defaults = { state = "available", private_dns_enabled = true, security_group_ids = ["sg-secret-endpoint"] }
  }
}

variables {
  aws_account_id             = "000000000000"
  bootstrap_admin_secret_arn = "arn:aws:secretsmanager:ap-northeast-2:000000000000:secret:rds!db-test-AbCdEf"
  bootstrap_expires_at       = timeadd(timestamp(), "2h")
  deployment = {
    vpc_id                            = "vpc-production"
    subnet_id                         = "subnet-private-a"
    cluster_arn                       = "arn:aws:ecs:ap-northeast-2:000000000000:cluster/seonjalal-v2-production-cluster"
    capacity_provider                 = "seonjalal-v2-production-ai"
    namespace_arn                     = "arn:aws:servicediscovery:ap-northeast-2:000000000000:namespace/ns-a1b2c3d4"
    database_host                     = "ai-db.example.ap-northeast-2.rds.amazonaws.com"
    database_client_security_group_id = "sg-ai-client"
    backend_security_group_id         = "sg-main-backend"
    backend_export_security_group_id  = "sg-backend-alb"
    backend_base_url                  = "http://main-backend:8080"
    source_sha                        = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    images = {
      chat     = "000000000000.dkr.ecr.ap-northeast-2.amazonaws.com/seonjalal/production/ai-chat@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
      profiler = "000000000000.dkr.ecr.ap-northeast-2.amazonaws.com/seonjalal/production/ai-profiler@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
      search   = "000000000000.dkr.ecr.ap-northeast-2.amazonaws.com/seonjalal/production/ai-search@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
      migrate  = "000000000000.dkr.ecr.ap-northeast-2.amazonaws.com/seonjalal/production/ai-migrate@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    }
    asset_bucket          = "ai-production-assets-example"
    asset_prefix          = "ai/production/verified-release/"
    asset_manifest_key    = "ai/production/verified-release/manifest.json"
    asset_manifest_sha256 = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    catalog_bundle_s3     = "s3://ai-production-assets-example/ai/production/verified-release/catalog/catalog-bundle-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.tar"
    provider_models       = { chat = "synthetic/model", profiler = "synthetic/model" }
    desired_count         = 0
  }
}

run "bootstrap_is_registered_but_not_run" {
  command = plan
  assert {
    condition = (
      length(aws_ecs_task_definition.bootstrap) == 1 &&
      module.ai[0].runtime.service_name == "seonjalal-v2-production-ai" &&
      jsondecode(aws_iam_role_policy.bootstrap_admin_secret[0].policy).Statement[0].Resource == var.bootstrap_admin_secret_arn &&
      length(aws_vpc_security_group_ingress_rule.secrets_from_bootstrap) == 1
    )
    error_message = "관리자 Secret 읽기·AI Secret 세 개 쓰기·Endpoint 접근만 준비하고 Task는 자동 실행하지 않아야 함."
  }
}
