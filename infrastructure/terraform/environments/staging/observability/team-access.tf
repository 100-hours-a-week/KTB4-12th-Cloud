variable "grafana_access_cidr" {
  description = "Grafana(3000)에 접속할 수 있는 출발지. 팀 Tailscale 서브넷 라우터(개발 서버)의 사설 IP 하나만 허용한다. 라우터는 전달 트래픽의 출발지를 자기 IP로 바꾼다."
  type        = string
  default     = "10.20.0.28/32"
  validation {
    condition     = can(regex("^10\\.20\\.\\d{1,3}\\.\\d{1,3}/32$", var.grafana_access_cidr)) && can(cidrhost(var.grafana_access_cidr, 0))
    error_message = "Grafana 접근 출발지는 10.20.x.x 사설 대역의 단일 IP(/32)여야 한다. 대역 전체나 공개 CIDR은 허용하지 않는다."
  }
}

# 기존에는 인바운드가 없었다. 팀원이 Tailscale 라우터를 거쳐 Grafana에만 접속하도록 3000 하나만 연다.
resource "aws_vpc_security_group_ingress_rule" "grafana_from_team_router" {
  security_group_id = aws_security_group.monitor.id
  ip_protocol       = "tcp"
  from_port         = 3000
  to_port           = 3000
  cidr_ipv4         = var.grafana_access_cidr
  description       = "Grafana from the team Tailscale subnet router only"
}
