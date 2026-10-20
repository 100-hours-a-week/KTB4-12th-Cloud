# AWS 연결 없이 운영 분리와 잘못된 네트워크 차단 검증.
mock_provider "aws" {}
variables {
  aws_account_id  = "000000000000"
  vpc_id          = "vpc-test"
  data_subnet_ids = ["subnet-data-a", "subnet-data-c"]
  enable_database = false
}
override_data {
  target = data.aws_subnet.data["subnet-data-a"]
  values = { vpc_id = "vpc-test", availability_zone = "ap-northeast-2a", map_public_ip_on_launch = false }
}
override_data {
  target = data.aws_subnet.data["subnet-data-c"]
  values = { vpc_id = "vpc-test", availability_zone = "ap-northeast-2c", map_public_ip_on_launch = false }
}
run "production_boundaries_only" {
  command = plan
  assert {
    condition     = aws_ecs_cluster.production.name == "seonjalal-v2-production-cluster" && aws_ecr_repository.backend.name == "seonjalal/production/backend" && aws_secretsmanager_secret.runtime.name == "seonjalal/production/backend/runtime"
    error_message = "운영 리소스 이름은 시험 환경과 분리 필요."
  }
  assert {
    condition     = !output.production_foundation.application_ready && !output.production_foundation.database_ready && !output.production_foundation.production_cutover_ready
    error_message = "기반 준비만으로 실행/전환 완료 표시 금지."
  }
  assert {
    condition     = aws_vpc_security_group_ingress_rule.backend_from_alb.from_port == 8080 && aws_vpc_security_group_ingress_rule.database_from_backend.from_port == 3306 && aws_ecr_repository.backend.image_tag_mutability == "IMMUTABLE"
    error_message = "운영 최소 연결 포트 및 이미지 불변성 유지 필요."
  }
}
run "reject_same_az" {
  command = plan
  override_data {
    target = data.aws_subnet.data["subnet-data-c"]
    values = { vpc_id = "vpc-test", availability_zone = "ap-northeast-2a", map_public_ip_on_launch = false }
  }
  expect_failures = [terraform_data.network_guard]
}
run "reject_other_vpc" {
  command = plan
  override_data {
    target = data.aws_subnet.data["subnet-data-c"]
    values = { vpc_id = "vpc-other", availability_zone = "ap-northeast-2c", map_public_ip_on_launch = false }
  }
  expect_failures = [terraform_data.network_guard]
}
run "reject_public_ip_assignment" {
  command = plan
  override_data {
    target = data.aws_subnet.data["subnet-data-c"]
    values = { vpc_id = "vpc-test", availability_zone = "ap-northeast-2c", map_public_ip_on_launch = true }
  }
  expect_failures = [terraform_data.network_guard]
}

run "production_database_protected_candidate" {
  command = plan
  variables { enable_database = true }
  assert {
    condition     = aws_db_instance.production[0].identifier == "seonjalal-v2-production" && aws_db_instance.production[0].engine_version == "8.4.11" && aws_db_instance.production[0].multi_az && !aws_db_instance.production[0].publicly_accessible
    error_message = "운영 DB는 별도 이름/고정 버전/비공개 Multi-AZ 필요."
  }
  assert {
    condition     = aws_db_instance.production[0].storage_encrypted && aws_db_instance.production[0].deletion_protection && !aws_db_instance.production[0].skip_final_snapshot && aws_db_instance.production[0].backup_retention_period == 7 && aws_db_instance.production[0].manage_master_user_password
    error_message = "운영 암호화/삭제보호/최종스냅샷/7일백업/관리형 비밀번호 유지 필요."
  }
  assert {
    condition     = aws_db_instance.production[0].allocated_storage == 20 && aws_db_instance.production[0].max_allocated_storage == 100 && !output.production_database_candidate.data_migration_ready && !output.production_foundation.production_cutover_ready
    error_message = "저장공간 상한 유지 및 빈 DB를 이전 완료로 표시 금지."
  }
}
run "reject_storage_cost_ceiling" {
  command = plan
  variables { database_max_storage_gib = 200 }
  expect_failures = [var.database_max_storage_gib]
}
