output "state_bucket_name" {
  description = "V2 Terraform Root Module이 사용할 State Bucket 이름"
  value       = aws_s3_bucket.terraform_state.id
}

output "bootstrap_backend_key" {
  description = "Bootstrap State를 S3로 이전할 때 사용할 Key"
  value       = "bootstrap/state/terraform.tfstate"
}

output "aws_account_id" {
  description = "Provider가 확인한 대상 AWS Account ID"
  value       = data.aws_caller_identity.current.account_id
}
