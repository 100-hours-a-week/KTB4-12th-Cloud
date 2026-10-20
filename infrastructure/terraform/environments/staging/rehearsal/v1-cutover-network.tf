# 기존 운영 공유 경로 대신 벤치마크 전용 경로만 관리.
variable "v1_cutover_network" {
  description = "격리 시험 경로. null이면 생성하지 않음. 승인된 리허설 대상만 입력."
  type = object({
    source_instance_id      = string
    production_instance_id  = string
    source_subnet_id        = string
    source_vpc_id           = string
    source_private_ip       = string
    baseline_route_table_id = string
    peering_id              = string
  })
  default = null
  validation {
    condition     = var.v1_cutover_network == null || (var.enable_alb && var.database_fixture_verified && !var.database_cutover_verified)
    error_message = "격리 경로는 ALB와 독립 Fixture가 활성화된 비전환 DB 리허설에서만 허용."
  }
}

locals {
  v1_cutover = var.v1_cutover_network == null ? {} : { rehearsal = var.v1_cutover_network }
  # 기존 Data 경로는 유지하고 ALB가 있는 App 두 대역만 추가.
  v1_cutover_peer_cidrs = var.v1_cutover_network == null ? toset([]) : toset([
    "10.20.20.0/24", "10.20.21.0/24", "10.20.10.0/24", "10.20.11.0/24"
  ])
  v1_cutover_app_subnets = var.v1_cutover_network == null ? toset([]) : toset(var.app_subnet_ids)
}

data "aws_instance" "cutover_source" {
  for_each    = local.v1_cutover
  instance_id = each.value.source_instance_id
}
data "aws_instance" "cutover_production_guard" {
  for_each    = local.v1_cutover
  instance_id = each.value.production_instance_id
}
data "aws_instances" "cutover_subnet_occupants" {
  for_each = local.v1_cutover
  filter {
    name   = "subnet-id"
    values = [each.value.source_subnet_id]
  }
  instance_state_names = ["pending", "running", "stopping", "stopped", "shutting-down"]
}
data "aws_subnet" "cutover_source" {
  for_each = local.v1_cutover
  id       = each.value.source_subnet_id
}
data "aws_route_table" "cutover_baseline" {
  for_each       = local.v1_cutover
  route_table_id = each.value.baseline_route_table_id
}
data "aws_vpc_peering_connection" "cutover" {
  for_each = local.v1_cutover
  id       = each.value.peering_id
}
data "aws_route_table" "cutover_app" {
  for_each  = local.v1_cutover_app_subnets
  subnet_id = each.value
}

resource "aws_route_table" "v1_cutover" {
  for_each = local.v1_cutover
  vpc_id   = each.value.source_vpc_id
  tags     = { Name = "${local.name_prefix}-v1-benchmark-only" }
  lifecycle {
    precondition {
      condition = (
        data.aws_instance.cutover_source[each.key].instance_state == "running" &&
        data.aws_instance.cutover_source[each.key].subnet_id == each.value.source_subnet_id &&
        data.aws_instance.cutover_source[each.key].private_ip == each.value.source_private_ip &&
        data.aws_subnet.cutover_source[each.key].vpc_id == each.value.source_vpc_id &&
        data.aws_instance.cutover_production_guard[each.key].subnet_id != each.value.source_subnet_id &&
        toset(data.aws_instances.cutover_subnet_occupants[each.key].ids) == toset([each.value.source_instance_id])
      )
      error_message = "벤치마크 한 대만 사용하는 별도 서브넷과 실행 IP 확인 필요. 운영 공유 서브넷 변경 금지."
    }
    precondition {
      condition = (
        data.aws_vpc_peering_connection.cutover[each.key].status == "active" &&
        data.aws_vpc_peering_connection.cutover[each.key].vpc_id == each.value.source_vpc_id &&
        data.aws_vpc_peering_connection.cutover[each.key].peer_vpc_id == var.vpc_id &&
        each.value.source_vpc_id != var.vpc_id &&
        toset([for subnet in values(data.aws_subnet.app) : subnet.cidr_block]) == toset(["10.20.10.0/24", "10.20.11.0/24"]) &&
        alltrue([for rt in values(data.aws_route_table.cutover_app) : rt.vpc_id == var.vpc_id])
      )
      error_message = "V1→V2의 활성 Peering 및 지정 App 서브넷·Route Table 확인 필요."
    }
    precondition {
      # Provider는 AWS 자동 local 경로를 조회 결과에서 제외함.
      # 관리 경로 세 개(인터넷 1 + Data 2)를 정확히 제한하여 누락 방지.
      condition = (
        data.aws_route_table.cutover_baseline[each.key].vpc_id == each.value.source_vpc_id &&
        length(data.aws_route_table.cutover_baseline[each.key].routes) == 3 &&
        length([for r in data.aws_route_table.cutover_baseline[each.key].routes : r if r.cidr_block == "0.0.0.0/0" && can(regex("^igw-", r.gateway_id))]) == 1 &&
        length([for r in data.aws_route_table.cutover_baseline[each.key].routes : r if contains(["10.20.20.0/24", "10.20.21.0/24"], r.cidr_block == null ? "" : r.cidr_block) && r.vpc_peering_connection_id == each.value.peering_id]) == 2
      )
      error_message = "보존 대상 Main 경로가 예상과 다름. 인터넷·Data 경로 및 추가 경로를 재검토하고 중단."
    }
  }
}

resource "aws_route" "v1_cutover_internet" {
  for_each               = local.v1_cutover
  route_table_id         = aws_route_table.v1_cutover[each.key].id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id = one([for route in data.aws_route_table.cutover_baseline[each.key].routes : route.gateway_id
  if route.cidr_block == "0.0.0.0/0"])
}
resource "aws_route" "v1_cutover_peering" {
  for_each                  = local.v1_cutover_peer_cidrs
  route_table_id            = aws_route_table.v1_cutover["rehearsal"].id
  destination_cidr_block    = each.value
  vpc_peering_connection_id = var.v1_cutover_network.peering_id
}
resource "aws_route" "v1_cutover_return" {
  for_each                  = local.v1_cutover_app_subnets
  route_table_id            = data.aws_route_table.cutover_app[each.key].id
  destination_cidr_block    = "${var.v1_cutover_network.source_private_ip}/32"
  vpc_peering_connection_id = var.v1_cutover_network.peering_id
  depends_on                = [aws_route_table.v1_cutover]
}
resource "aws_vpc_security_group_ingress_rule" "alb_from_v1_cutover" {
  for_each          = local.v1_cutover
  security_group_id = aws_security_group.alb.id
  description       = "HTTPS from isolated V1 benchmark only"
  cidr_ipv4         = "${each.value.source_private_ip}/32"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  depends_on        = [aws_route_table.v1_cutover]
}
resource "aws_route_table_association" "v1_cutover" {
  for_each       = local.v1_cutover
  subnet_id      = each.value.source_subnet_id
  route_table_id = aws_route_table.v1_cutover[each.key].id
  # 모든 경로와 최소 접근 규칙을 준비한 뒤 서브넷 연결 변경.
  depends_on = [aws_route.v1_cutover_internet, aws_route.v1_cutover_peering, aws_route.v1_cutover_return, aws_vpc_security_group_ingress_rule.alb_from_v1_cutover]
}
