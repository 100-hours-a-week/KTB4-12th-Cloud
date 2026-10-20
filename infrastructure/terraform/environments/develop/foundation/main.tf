module "foundation_network" {
  source = "../../../modules/foundation_network"

  name_prefix        = local.name_prefix
  vpc_cidr           = var.vpc_cidr
  availability_zones = local.availability_zones
  subnet_cidrs       = local.subnet_cidrs
  application_port   = var.application_port
  nat_gateway_mode   = var.nat_gateway_mode
}
