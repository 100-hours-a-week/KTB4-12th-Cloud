locals {
  host_metric_groups = {
    v1_benchmark = {
      id     = "sg-01e7be27d82b62ccc"
      vpc_id = "vpc-0dcb5284ffb90203a"
    }
    v2_staging = {
      id     = "sg-0c234b36a68c785f2"
      vpc_id = local.vpc_id
    }
    v2_develop = {
      id     = "sg-078dbf2076f401c83"
      vpc_id = local.vpc_id
    }
  }
}

data "aws_security_group" "host_metrics" {
  for_each = local.host_metric_groups
  id       = each.value.id
}

# 기존 서버의 SG에 독립 규칙만 추가. 앱 포트와 기존 inline 규칙은 수정하지 않음.
# V1은 Peering 경유 원본 private IP 보존을 전제로 /32만 허용.
resource "aws_vpc_security_group_ingress_rule" "node_exporter" {
  for_each          = local.host_metric_groups
  security_group_id = data.aws_security_group.host_metrics[each.key].id
  ip_protocol       = "tcp"
  from_port         = 9100
  to_port           = 9100
  cidr_ipv4         = "${aws_instance.monitor.private_ip}/32"
  description       = "Node metrics from dedicated private staging monitor only"

  lifecycle {
    precondition {
      condition     = data.aws_security_group.host_metrics[each.key].vpc_id == each.value.vpc_id
      error_message = "조회한 benchmark/staging/develop 대상 SG의 VPC 불일치. 운영 SG 변경 금지."
    }
  }
}

# 모니터에서 V1 benchmark 딱1대의 9100만 허용. 기존 Peering/Route는 변경 안함.
resource "aws_vpc_security_group_egress_rule" "node_exporter_v1" {
  security_group_id = aws_security_group.monitor.id
  ip_protocol       = "tcp"
  from_port         = 9100
  to_port           = 9100
  cidr_ipv4         = "172.31.6.212/32"
  description       = "Scrape V1 benchmark node exporter via existing peering"
}

# ECS 호스트 교체 시 SG 소속 범위는 유지. 실제 scrape IP 갱신은 별도 수집 설정 책임.
resource "aws_vpc_security_group_egress_rule" "node_exporter_v2" {
  security_group_id            = aws_security_group.monitor.id
  referenced_security_group_id = data.aws_security_group.host_metrics["v2_staging"].id
  ip_protocol                  = "tcp"
  from_port                    = 9100
  to_port                      = 9100
  description                  = "Scrape V2 staging ECS host node exporters only"
}

# Develop 한 대의 실제 IP만 허용. IP 변경 시 재조회 후 명시적 갱신 필요.
resource "aws_vpc_security_group_egress_rule" "node_exporter_develop" {
  security_group_id = aws_security_group.monitor.id
  ip_protocol       = "tcp"
  from_port         = 9100
  to_port           = 9100
  cidr_ipv4         = "10.20.0.28/32"
  description       = "Scrape V2 develop host node exporter only"
}
