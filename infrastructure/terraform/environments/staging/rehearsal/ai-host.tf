variable "ai_host_enabled" {
  description = "스테이징 AI 전용 ECS 호스트(시작 템플릿·ASG·용량 공급자)를 만든다. 꺼져 있으면 아무것도 만들지 않는다."
  type        = bool
  default     = false
}

variable "ai_desired_capacity" {
  description = "AI 전용 호스트 수. 기본 0(꺼짐 기준선). 켜기·끄기는 scripts/staging-power.py가 담당한다."
  type        = number
  default     = 0
  validation {
    condition     = contains([0, 1], var.ai_desired_capacity)
    error_message = "스테이징 AI 전용 호스트는 0대 또는 1대만 허용한다."
  }
  validation {
    condition     = var.ai_desired_capacity == 0 || var.ai_host_enabled
    error_message = "ai_host_enabled를 켜기 전에는 AI 호스트 수를 올릴 수 없다."
  }
}

locals {
  # 운영 AI와 같은 서울 A 존의 Private App Subnet만 사용한다(단일 Task, 가용성보다 비용 우선).
  ai_app_subnet_ids = [for id, subnet in data.aws_subnet.app : id if subnet.availability_zone == "ap-northeast-2a"]
}

# 운영 production/runtime의 AI 호스트와 같은 구성: t3.medium, IMDSv2, 암호화 gp3 30GiB, 예약 메모리 512MiB.
# BE 호스트의 IAM 인스턴스 프로필·보안 그룹을 재사용한다(운영도 같은 노드 그룹을 공유).
resource "aws_launch_template" "ai" {
  count                  = var.ai_host_enabled ? 1 : 0
  name_prefix            = "${local.name_prefix}-ai-"
  image_id               = coalesce(var.ecs_optimized_ami_id, data.aws_ssm_parameter.ecs_optimized_ami.value)
  instance_type          = "t3.medium"
  vpc_security_group_ids = [aws_security_group.ecs_container_instance.id]

  iam_instance_profile {
    arn = aws_iam_instance_profile.ecs_container_instance.arn
  }

  user_data = base64encode(<<-SH
    #!/bin/bash
    set -eu
    printf '%s\n' \
      'ECS_CLUSTER=${aws_ecs_cluster.this.name}' \
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
    tags          = { Name = "${local.name_prefix}-ecs-ai" }
  }
  tag_specifications {
    resource_type = "volume"
    tags          = { Name = "${local.name_prefix}-ecs-ai" }
  }

  lifecycle {
    create_before_destroy = true
  }
}

# min 0·max 1. 사용하지 않을 때는 0이며 켜기·끄기는 전원 스크립트가 desired를 바꾼다(Terraform은 꺼짐 기준선).
resource "aws_autoscaling_group" "ai" {
  count                     = var.ai_host_enabled ? 1 : 0
  name                      = "${local.name_prefix}-ecs-ai"
  min_size                  = 0
  desired_capacity          = var.ai_desired_capacity
  max_size                  = 1
  vpc_zone_identifier       = local.ai_app_subnet_ids
  health_check_type         = "EC2"
  health_check_grace_period = 300

  launch_template {
    id      = aws_launch_template.ai[0].id
    version = tostring(aws_launch_template.ai[0].latest_version)
  }

  tag {
    key                 = "Name"
    value               = "${local.name_prefix}-ecs-ai"
    propagate_at_launch = true
  }
  tag {
    key                 = "AmazonECSManaged"
    value               = "true"
    propagate_at_launch = true
  }

  lifecycle {
    precondition {
      condition     = length(local.ai_app_subnet_ids) == 1
      error_message = "AI 호스트는 서울 A 존의 Private App Subnet 하나에만 배치한다."
    }
  }
}

resource "aws_ecs_capacity_provider" "ai" {
  count = var.ai_host_enabled ? 1 : 0
  name  = "${local.name_prefix}-ai"

  auto_scaling_group_provider {
    auto_scaling_group_arn         = aws_autoscaling_group.ai[0].arn
    managed_draining               = "ENABLED"
    managed_termination_protection = "DISABLED"

    managed_scaling {
      status = "DISABLED"
    }
  }
}
