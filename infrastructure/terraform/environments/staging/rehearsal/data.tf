data "aws_ssm_parameter" "ecs_optimized_ami" {
  name = "/aws/service/ecs/optimized-ami/amazon-linux-2023/recommended/image_id"
}
data "aws_subnet" "app" {
  for_each = toset(var.app_subnet_ids)
  id       = each.value
}
data "aws_subnet" "public" {
  for_each = toset(var.public_subnet_ids)
  id       = each.value
}
# 조회만 수행하며 기존 Develop EC2/SG/Source-Destination Check를 수정하지 않음.
data "aws_instance" "tailscale_router" {
  count       = var.enable_alb && var.tailscale_router_instance_id != null ? 1 : 0
  instance_id = var.tailscale_router_instance_id
  lifecycle {
    postcondition {
      condition     = self.instance_state == "running" && can(cidrhost("${self.private_ip}/32", 0))
      error_message = "Tailscale 진입 EC2는 유효한 Private IP가 있는 실행 중인 인스턴스여야 함."
    }
  }
}
data "aws_subnet" "tailscale_router" {
  count = var.enable_alb && var.tailscale_router_instance_id != null ? 1 : 0
  id    = data.aws_instance.tailscale_router[0].subnet_id
  lifecycle {
    postcondition {
      condition     = self.vpc_id == var.vpc_id
      error_message = "Tailscale 진입 EC2의 서브넷은 스테이징과 같은 VPC여야 함."
    }
  }
}
# 데이터 조회만 수행. 기존 Develop Foundation State를 연결하거나 수정하지 않음.
resource "terraform_data" "network_guard" {
  lifecycle {
    precondition {
      condition     = alltrue([for s in values(data.aws_subnet.app) : s.vpc_id == var.vpc_id]) && alltrue([for s in values(data.aws_subnet.public) : s.vpc_id == var.vpc_id])
      error_message = "선택한 Subnet은 모두 지정 VPC에 속해야 합니다."
    }
    precondition {
      condition     = length(toset([for s in values(data.aws_subnet.app) : s.availability_zone])) == 2 && length(toset([for s in values(data.aws_subnet.public) : s.availability_zone])) == 2
      error_message = "App/Public Subnet 각각 서로 다른 두 AZ 필요."
    }
    precondition {
      condition     = alltrue([for s in values(data.aws_subnet.app) : !s.map_public_ip_on_launch]) && length(setintersection(toset(var.app_subnet_ids), toset(var.public_subnet_ids))) == 0
      error_message = "Private App Subnet은 Public 목록과 분리하고 자동 Public IP 비활성화 필요. Route/Egress는 별도 실조회 검증."
    }
    # 기존 develop App Subnet 재사용 허용. 태그 변경 없이 Egress를 별도 검증.
  }
}
