resource "aws_security_group" "develop" {
  name        = "${local.name_prefix}-runtime-sg"
  description = "No public ingress; access is provided through SSM and Tailscale"
  vpc_id      = local.foundation_network.vpc_id

  tags = {
    Name = "${local.name_prefix}-runtime-sg"
  }
}

resource "aws_vpc_security_group_egress_rule" "develop_ipv4" {
  security_group_id = aws_security_group.develop.id
  description       = "Outbound access for SSM, ECR, Tailscale, Grafana Cloud and external APIs"
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_instance" "develop" {
  ami                         = data.aws_ami.amazon_linux_2023.id
  instance_type               = var.instance_type
  subnet_id                   = local.public_subnet_a_id
  associate_public_ip_address = true
  vpc_security_group_ids      = [aws_security_group.develop.id]
  iam_instance_profile        = aws_iam_instance_profile.develop.name

  disable_api_termination              = true
  instance_initiated_shutdown_behavior = "stop"
  monitoring                           = false

  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required"
    # Docker Bridge 내부 Backend가 IMDSv2로 Instance Role 자격 증명을 읽으려면 Hop Limit 2가 필요하다.
    http_put_response_hop_limit = 2
    instance_metadata_tags      = "disabled"
  }

  root_block_device {
    encrypted             = true
    volume_type           = "gp3"
    volume_size           = var.root_volume_size_gib
    delete_on_termination = true

    tags = {
      Name = "${local.name_prefix}-runtime-root"
    }
  }

  user_data = templatefile("${path.module}/user_data.sh.tftpl", {
    bootstrap_version = var.bootstrap_version
  })

  tags = {
    Name = "${local.name_prefix}-runtime"
  }
}
