locals {
  name      = "seonjalal-v2-staging-observability"
  vpc_id    = "vpc-058d65b38801b9951"
  subnet_id = "subnet-08c2017235a4a56da"
}

data "aws_subnet" "monitor" {
  id = local.subnet_id
}

# AMI ID는 첫 Plan에서 확인. 이후 Parameter 갱신으로 기존 노드를 자동 교체하지 않음.
data "aws_ssm_parameter" "al2023" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

resource "aws_iam_role" "monitor" {
  name = local.name
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.monitor.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "monitor" {
  name = local.name
  role = aws_iam_role.monitor.name
}

# 인바운드 없음. Grafana는 이후 localhost 바인딩 및 SSM 포트 전달로 접근.
resource "aws_security_group" "monitor" {
  name        = local.name
  description = "Private monitoring host; no inbound rules; SSM access only"
  vpc_id      = local.vpc_id
}

# 기존 NAT를 통해 SSM/API/패키지 다운로드에 필요한 HTTPS만 허용.
# Exporter scrape는 대상과 포트를 확인한 후 별도 규칙으로 추가.
resource "aws_vpc_security_group_egress_rule" "https" {
  security_group_id = aws_security_group.monitor.id
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = "0.0.0.0/0"
  description       = "HTTPS via existing NAT for SSM and installation"
}

resource "aws_instance" "monitor" {
  ami                         = data.aws_ssm_parameter.al2023.value
  instance_type               = "t3.small"
  subnet_id                   = local.subnet_id
  associate_public_ip_address = false
  vpc_security_group_ids      = [aws_security_group.monitor.id]
  iam_instance_profile        = aws_iam_instance_profile.monitor.name

  # 장기 고CPU 모니터링 시 무제한 T3 크레딧 추가 과금 방지.
  # Standard 크레딧 소진 시 수집 지연 여부를 관측하고 사양을 재검토.
  credit_specification {
    cpu_credits = "standard"
  }
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }
  root_block_device {
    encrypted             = true
    volume_type           = "gp3"
    volume_size           = 30
    delete_on_termination = true
  }
  tags = { Name = local.name }

  # 부트스트랩/컨테이너/Secret/기존 서버 변경 없음. SSM 연결 확인 후 설치.
  lifecycle {
    ignore_changes  = [ami]
    prevent_destroy = true
    precondition {
      condition = (
        data.aws_subnet.monitor.vpc_id == local.vpc_id &&
        data.aws_subnet.monitor.cidr_block == "10.20.10.0/24" &&
        data.aws_subnet.monitor.available_ip_address_count >= 10
      )
      error_message = "확인된 V2 private App-A 서브넷 및 여유 IP 조건 필요."
    }
  }
  depends_on = [aws_iam_role_policy_attachment.ssm, aws_vpc_security_group_egress_rule.https]
}

output "staging_observability" {
  value = {
    instance_id       = aws_instance.monitor.id
    private_ip        = aws_instance.monitor.private_ip
    security_group_id = aws_security_group.monitor.id
    instance_type     = aws_instance.monitor.instance_type
    root_gib          = 30
    access            = "SSM only; no public IP or inbound rules"
    monitoring_ready  = false
    note              = "Foundation only. Prometheus/Grafana/exporters/CloudWatch IAM not installed."
  }
}
