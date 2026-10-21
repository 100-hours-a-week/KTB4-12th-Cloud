locals {
  name = "seonjalal-v2-production"
  # 사용자 조회에서 기존 NAT 경로를 확인한 App 서브넷만 사용.
  app_subnets      = toset(["subnet-08c2017235a4a56da", "subnet-0f778fcbf52c9df2f"])
  ai_app_subnet_id = "subnet-08c2017235a4a56da"
}

data "aws_ecs_cluster" "production" {
  cluster_name = "${local.name}-cluster"
}

data "aws_subnet" "app" {
  for_each = local.app_subnets
  id       = each.value
}

# 현재 권장 x86_64 ECS AMI는 저장된 Plan에서 고정. 실행 중 자동 AMI 교체 없음.
data "aws_ssm_parameter" "ecs_ami" {
  name = "/aws/service/ecs/optimized-ami/amazon-linux-2023/recommended/image_id"
}

resource "aws_iam_role" "node" {
  name = "${local.name}-ecs-node"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow", Principal = { Service = "ec2.amazonaws.com" }, Action = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "ecs_node" {
  role       = aws_iam_role.node.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonEC2ContainerServiceforEC2Role"
}

resource "aws_iam_role_policy_attachment" "ssm_node" {
  role       = aws_iam_role.node.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "node" {
  name = "${local.name}-ecs-node"
  role = aws_iam_role.node.name
}

# 호스트 관리 접속은 SSM. SSH 및 인터넷 인바운드 규칙 없음.
resource "aws_security_group" "node" {
  name        = "${local.name}-ecs-node"
  description = "Production ECS hosts; no inbound administration ports"
  vpc_id      = data.aws_subnet.app[sort(tolist(local.app_subnets))[0]].vpc_id
}

resource "aws_vpc_security_group_egress_rule" "node_https" {
  security_group_id = aws_security_group.node.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  description       = "ECS, ECR, SSM and Logs HTTPS through existing NAT or endpoints"
}

resource "aws_launch_template" "node" {
  name_prefix            = "${local.name}-ecs-"
  image_id               = data.aws_ssm_parameter.ecs_ami.value
  instance_type          = "t3.medium"
  vpc_security_group_ids = [aws_security_group.node.id]
  iam_instance_profile {
    arn = aws_iam_instance_profile.node.arn
  }
  user_data = base64encode(<<-SH
    #!/bin/bash
    set -eu
    # 기존 운영 Cluster에 등록. 업무 앱 기동 또는 DB 접속 없음.
    printf '%s\n' \
      'ECS_CLUSTER=${data.aws_ecs_cluster.production.cluster_name}' \
      'ECS_ENABLE_TASK_IAM_ROLE=true' \
      'ECS_AWSVPC_BLOCK_IMDS=true' \
      'ECS_RESERVED_MEMORY=512' >> /etc/ecs/ecs.config
  SH
  )
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
  }
  block_device_mappings {
    device_name = "/dev/xvda"
    ebs {
      volume_type           = "gp3"
      volume_size           = 30
      encrypted             = true
      delete_on_termination = true
    }
  }
  tag_specifications {
    resource_type = "instance"
    tags          = { Name = "${local.name}-ecs-application" }
  }
  tag_specifications {
    resource_type = "volume"
    tags          = { Name = "${local.name}-ecs-application" }
  }
  lifecycle {
    create_before_destroy = true
    precondition {
      condition = (
        length(toset([for subnet in data.aws_subnet.app : subnet.vpc_id])) == 1 &&
        length(toset([for subnet in data.aws_subnet.app : subnet.availability_zone])) == 2 &&
        alltrue([for subnet in data.aws_subnet.app : !subnet.map_public_ip_on_launch])
      )
      error_message = "동일 VPC·서로 다른 2개 AZ·공인 IP 자동 할당 없는 App Subnet 확인 필요."
    }
  }
}

# 초기 비용 절감안: 상시 1대. 호스트 장애 시 무중단 보장 없음.
# Task/Service는 미정의. 승인 전 Plan만으로 EC2가 기동되지는 않음.
resource "aws_autoscaling_group" "node" {
  name                      = "${local.name}-ecs-application"
  min_size                  = 1
  desired_capacity          = 1
  max_size                  = 2
  vpc_zone_identifier       = sort(tolist(local.app_subnets))
  health_check_type         = "EC2"
  health_check_grace_period = 300
  launch_template {
    id      = aws_launch_template.node.id
    version = tostring(aws_launch_template.node.latest_version)
  }
  tag {
    key                 = "Name"
    value               = "${local.name}-ecs-application"
    propagate_at_launch = true
  }
  tag {
    key                 = "AmazonECSManaged"
    value               = "true"
    propagate_at_launch = true
  }
  depends_on = [
    aws_iam_role_policy_attachment.ecs_node,
    aws_iam_role_policy_attachment.ssm_node,
    aws_vpc_security_group_egress_rule.node_https,
  ]
}

resource "aws_ecs_capacity_provider" "production" {
  name = "${local.name}-application"
  auto_scaling_group_provider {
    auto_scaling_group_arn         = aws_autoscaling_group.node.arn
    managed_draining               = "ENABLED"
    managed_termination_protection = "DISABLED"
    managed_scaling {
      status = "DISABLED"
    }
  }
}

# AI는 BE 호스트와 용량을 공유하지 않는다. 기본 0대이며 별도 승인 후 1대로 기동.
resource "aws_launch_template" "ai" {
  name_prefix            = "${local.name}-ai-"
  image_id               = data.aws_ssm_parameter.ecs_ami.value
  instance_type          = "t3.medium"
  vpc_security_group_ids = [aws_security_group.node.id]
  iam_instance_profile {
    arn = aws_iam_instance_profile.node.arn
  }
  user_data = base64encode(<<-SH
    #!/bin/bash
    set -eu
    printf '%s\n' \
      'ECS_CLUSTER=${data.aws_ecs_cluster.production.cluster_name}' \
      'ECS_ENABLE_TASK_IAM_ROLE=true' \
      'ECS_AWSVPC_BLOCK_IMDS=true' \
      'ECS_RESERVED_MEMORY=512' >> /etc/ecs/ecs.config
  SH
  )
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
  }
  block_device_mappings {
    device_name = "/dev/xvda"
    ebs {
      volume_type           = "gp3"
      volume_size           = 30
      encrypted             = true
      delete_on_termination = true
    }
  }
  tag_specifications {
    resource_type = "instance"
    tags          = { Name = "${local.name}-ecs-ai" }
  }
  tag_specifications {
    resource_type = "volume"
    tags          = { Name = "${local.name}-ecs-ai" }
  }
  lifecycle {
    create_before_destroy = true
    precondition {
      condition = (
        data.aws_subnet.app[local.ai_app_subnet_id].availability_zone == "ap-northeast-2a" &&
        !data.aws_subnet.app[local.ai_app_subnet_id].map_public_ip_on_launch &&
        data.aws_subnet.app[local.ai_app_subnet_id].vpc_id == aws_security_group.node.vpc_id
      )
      error_message = "AI 호스트는 운영 VPC의 서울 A Private App Subnet에만 배치해야 함."
    }
  }
}

resource "aws_autoscaling_group" "ai" {
  name                      = "${local.name}-ecs-ai"
  min_size                  = 0
  desired_capacity          = var.ai_desired_capacity
  max_size                  = 1
  vpc_zone_identifier       = [local.ai_app_subnet_id]
  health_check_type         = "EC2"
  health_check_grace_period = 300
  launch_template {
    id      = aws_launch_template.ai.id
    version = tostring(aws_launch_template.ai.latest_version)
  }
  tag {
    key                 = "Name"
    value               = "${local.name}-ecs-ai"
    propagate_at_launch = true
  }
  tag {
    key                 = "AmazonECSManaged"
    value               = "true"
    propagate_at_launch = true
  }
  depends_on = [
    aws_iam_role_policy_attachment.ecs_node,
    aws_iam_role_policy_attachment.ssm_node,
    aws_vpc_security_group_egress_rule.node_https,
  ]
}

resource "aws_ecs_capacity_provider" "ai" {
  name = "${local.name}-ai"
  auto_scaling_group_provider {
    auto_scaling_group_arn         = aws_autoscaling_group.ai.arn
    managed_draining               = "ENABLED"
    managed_termination_protection = "DISABLED"
    managed_scaling {
      status = "DISABLED"
    }
  }
}

# 기존 BE 공급자와 기본 전략을 유지하면서 AI 전용 공급자만 추가한다.
resource "aws_ecs_cluster_capacity_providers" "production" {
  cluster_name       = data.aws_ecs_cluster.production.cluster_name
  capacity_providers = [aws_ecs_capacity_provider.production.name, aws_ecs_capacity_provider.ai.name]
  default_capacity_provider_strategy {
    capacity_provider = aws_ecs_capacity_provider.production.name
    base              = 1
    weight            = 1
  }
  lifecycle {
    precondition {
      condition     = data.aws_ecs_cluster.production.status == "ACTIVE"
      error_message = "운영 Cluster가 ACTIVE가 아니므로 연결 중단."
    }
  }
}
