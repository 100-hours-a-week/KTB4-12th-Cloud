output "boundary" {
  description = "실제 Resource 구현 전 검토할 V2 Develop Foundation 경계"
  value = {
    account_id         = var.aws_account_id
    region             = var.aws_region
    environment        = var.environment
    name_prefix        = local.name_prefix
    vpc_cidr           = var.vpc_cidr
    availability_zones = local.availability_zones
    subnet_cidrs       = local.subnet_cidrs
    required_tags      = local.required_tags
  }
}

output "network" {
  description = "A4 이후 Root에서 참조할 V2 Develop Network 경계"
  value = {
    vpc_id             = module.foundation_network.vpc_id
    public_subnet_ids  = module.foundation_network.public_subnet_ids
    app_subnet_ids     = module.foundation_network.app_subnet_ids
    data_subnet_ids    = module.foundation_network.data_subnet_ids
    security_group_ids = module.foundation_network.security_group_ids
    s3_endpoint_id     = module.foundation_network.s3_vpc_endpoint_id
    app_egress         = module.foundation_network.app_egress
  }
}
