resource "aws_launch_template" "application" {
  name_prefix = "${local.name_prefix}-application-"
  # 첫 탐색과 시험 중 호스트 업데이트를 분리. 고정값이 있으면 최신 권장값 이동 무시.
  image_id      = coalesce(var.ecs_optimized_ami_id, data.aws_ssm_parameter.ecs_optimized_ami.value)
  instance_type = var.container_instance_types[0]

  iam_instance_profile {
    arn = aws_iam_instance_profile.ecs_container_instance.arn
  }

  vpc_security_group_ids = [aws_security_group.ecs_container_instance.id]
  user_data = base64encode(templatefile("${path.module}/user_data.sh.tftpl", {
    cluster_name = aws_ecs_cluster.this.name
  }))

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
    instance_metadata_tags      = "enabled"
  }

  block_device_mappings {
    device_name = "/dev/xvda"

    ebs {
      delete_on_termination = true
      encrypted             = true
      volume_size           = var.root_volume_size_gib
      volume_type           = "gp3"
    }
  }

  monitoring {
    enabled = true
  }

  tag_specifications {
    resource_type = "instance"
    tags = {
      Name         = "${local.name_prefix}-ecs-application"
      CapacityType = "application"
    }
  }

  tag_specifications {
    resource_type = "volume"
    tags = {
      Name = "${local.name_prefix}-ecs-application"
    }
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_autoscaling_group" "application" {
  name                      = "${local.name_prefix}-ecs-application"
  min_size                  = var.asg_min_size
  desired_capacity          = var.asg_desired_capacity
  max_size                  = var.asg_max_size
  vpc_zone_identifier       = var.app_subnet_ids
  health_check_type         = "EC2"
  health_check_grace_period = 300
  protect_from_scale_in     = true

  mixed_instances_policy {
    launch_template {
      launch_template_specification {
        launch_template_id = aws_launch_template.application.id
        version            = "$Latest"
      }

      dynamic "override" {
        for_each = toset(var.container_instance_types)

        content {
          instance_type = override.value
        }
      }
    }
  }

  instance_refresh {
    strategy = "Rolling"

    preferences {
      min_healthy_percentage = 100
    }
  }

  tag {
    key                 = "Name"
    value               = "${local.name_prefix}-ecs-application"
    propagate_at_launch = true
  }

  tag {
    key                 = "AmazonECSManaged"
    value               = "true"
    propagate_at_launch = true
  }

  tag {
    key                 = "CapacityType"
    value               = "application"
    propagate_at_launch = true
  }

  # ASG의 Provider default_tags만으로는 EC2 전파를 보장하지 않음.
  # 새 앱 노드도 같은 접근 경계를 갖도록 세 태그를 명시적으로 전파.
  dynamic "tag" {
    for_each = {
      Project       = local.required_tags.Project
      Environment   = local.required_tags.Environment
      TeamSSMAccess = "true"
    }
    content {
      key                 = tag.key
      value               = tag.value
      propagate_at_launch = true
    }
  }

  lifecycle {
    precondition {
      condition = (
        var.asg_min_size >= 0 &&
        var.asg_min_size == floor(var.asg_min_size) &&
        var.asg_desired_capacity == floor(var.asg_desired_capacity) &&
        var.asg_max_size == floor(var.asg_max_size) &&
        var.asg_max_size > 0 &&
        var.asg_desired_capacity >= var.asg_min_size &&
        var.asg_max_size >= var.asg_desired_capacity
      )
      error_message = "ASG Capacity는 정수이고 0 <= min <= desired <= max, max > 0이어야 합니다."
    }

    # 빈 ASG 또는 제한된 점검용 1대 외에는 검증된 Private Egress 필요.
    # VPC/NAT/Route는 이 Root에서 생성·변경하지 않는다.
    precondition {
      condition = (
        var.asg_desired_capacity == 0 ||
        var.private_egress_verified ||
        var.egress_probe_mode
      )
      error_message = "EC2 기동에는 검증된 Egress 또는 앱 없는 단일 EC2 점검 모드 필요."
    }
    precondition {
      condition = !var.egress_probe_mode || (
        var.asg_min_size == 0 && var.asg_desired_capacity == 1 &&
        !var.enable_service && !var.enable_alb && !var.managed_scaling_enabled
      )
      error_message = "점검 모드는 min0/desired1·앱/ALB/ManagedScaling 비활성 상태만 허용."
    }
  }
}

resource "aws_ecs_capacity_provider" "application" {
  name = "${local.name_prefix}-application"

  auto_scaling_group_provider {
    auto_scaling_group_arn = aws_autoscaling_group.application.arn
    managed_draining       = "ENABLED"
    # Managed Termination Protection은 Managed Scaling 활성화 시에만 함께 사용한다.
    managed_termination_protection = var.managed_scaling_enabled ? "ENABLED" : "DISABLED"

    managed_scaling {
      status                    = var.managed_scaling_enabled ? "ENABLED" : "DISABLED"
      target_capacity           = 100
      minimum_scaling_step_size = 1
      maximum_scaling_step_size = 2
      instance_warmup_period    = 300
    }
  }
}
