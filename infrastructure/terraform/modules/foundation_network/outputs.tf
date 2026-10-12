output "vpc_id" {
  description = "V2 Foundation VPC ID"
  value       = aws_vpc.this.id
}

output "public_subnet_ids" {
  description = "AZ Key별 Public Subnet ID"
  value       = { for key in local.az_keys : key => aws_subnet.this["public-${key}"].id }
}

output "app_subnet_ids" {
  description = "AZ Key별 Private App Subnet ID"
  value       = { for key in local.az_keys : key => aws_subnet.this["app-${key}"].id }
}

output "data_subnet_ids" {
  description = "AZ Key별 Private Data Subnet ID"
  value       = { for key in local.az_keys : key => aws_subnet.this["data-${key}"].id }
}

output "security_group_ids" {
  description = "후속 ALB, ECS, RDS Root가 사용할 Security Group ID"
  value = {
    alb = aws_security_group.alb.id
    ecs = aws_security_group.ecs.id
    rds = aws_security_group.rds.id
  }
}

output "s3_vpc_endpoint_id" {
  description = "App Route Table에 연결된 S3 Gateway Endpoint ID"
  value       = aws_vpc_endpoint.s3.id
}
