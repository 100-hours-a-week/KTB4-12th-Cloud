resource "aws_launch_template" "application" {
  name_prefix   = "${local.name_prefix}-application-"
  image_id      = data.aws_ssm_parameter.ecs_optimized_ami.value
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
  vpc_zone_identifier       = local.app_subnet_ids
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

    # 빈 ASG는 Egress 없이 생성 가능. EC2 기동 시에는 제어면 통신 경로가 먼저 필요하다.
    # 외부 Egress Root를 도입하면 해당 Root의 검증된 Output을 이 조건에 연결한다.
    precondition {
      condition = (
        var.asg_desired_capacity == 0 ||
        contains(["single", "per_az"], try(local.foundation_network.app_egress.mode, "none"))
      )
      error_message = "EC2 기동 전에 Private App Subnet의 Egress를 구성해야 합니다. 별도 NAT Instance/Endpoint 도입 시 검증된 Output 계약도 먼저 연결해야 합니다."
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

check "two_app_subnets_are_configured" {
  assert {
    condition     = length(local.app_subnet_ids) == 2
    error_message = "ECS Multi-AZ 배치를 위해 App Subnet이 정확히 두 개 필요합니다."
  }
}
