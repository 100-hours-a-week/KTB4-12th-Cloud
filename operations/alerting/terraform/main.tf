locals {
  name_prefix   = "seonjalal-${var.environment}"
  lambda_name   = "${local.name_prefix}-discord-alert"
  alarm_actions = var.alarm_actions_enabled ? [aws_sns_topic.infrastructure_alerts.arn] : []
}

data "archive_file" "discord_alert" {
  type        = "zip"
  source_file = "${path.module}/../lambda/handler.py"
  output_path = "${path.module}/discord-alert.zip"
}

resource "aws_sns_topic" "infrastructure_alerts" {
  name = "${local.name_prefix}-infrastructure-alerts"
}

resource "aws_iam_role" "discord_alert" {
  name = "${local.lambda_name}-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Service = "lambda.amazonaws.com"
      }
      Action = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_basic_execution" {
  role       = aws_iam_role.discord_alert.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "read_discord_webhook" {
  name = "read-discord-webhook"
  role = aws_iam_role.discord_alert.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid      = "ReadOnlyDiscordWebhook"
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = var.discord_webhook_secret_arn
    }]
  })
}

resource "aws_cloudwatch_log_group" "discord_alert" {
  name              = "/aws/lambda/${local.lambda_name}"
  retention_in_days = 30
}

resource "aws_lambda_function" "discord_alert" {
  function_name = local.lambda_name
  description   = "Forward Seonjalal infrastructure alerts from SNS to Discord"
  role          = aws_iam_role.discord_alert.arn
  runtime       = "python3.12"
  handler       = "handler.lambda_handler"

  filename         = data.archive_file.discord_alert.output_path
  source_code_hash = data.archive_file.discord_alert.output_base64sha256

  # 현재 계정의 Region 동시 실행 한도가 10이므로 별도 예약 없이 계정 공용 한도를 사용한다.
  memory_size = 128
  timeout     = 10

  environment {
    variables = {
      DISCORD_WEBHOOK_SECRET_ARN = var.discord_webhook_secret_arn
      DISCORD_USERNAME           = "Seonjalal Infra Alert"
      ENVIRONMENT                = var.environment
      SERVICE_NAME               = "Seonjalal AWS Infrastructure"
    }
  }

  depends_on = [
    aws_cloudwatch_log_group.discord_alert,
    aws_iam_role_policy_attachment.lambda_basic_execution,
    aws_iam_role_policy.read_discord_webhook,
  ]
}

resource "aws_lambda_permission" "allow_sns" {
  statement_id  = "AllowExecutionFromSNS"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.discord_alert.function_name
  principal     = "sns.amazonaws.com"
  source_arn    = aws_sns_topic.infrastructure_alerts.arn
}

resource "aws_sns_topic_subscription" "discord_alert" {
  topic_arn = aws_sns_topic.infrastructure_alerts.arn
  protocol  = "lambda"
  endpoint  = aws_lambda_function.discord_alert.arn

  depends_on = [aws_lambda_permission.allow_sns]
}

resource "aws_cloudwatch_metric_alarm" "system_status_failed" {
  alarm_name          = "${local.name_prefix}-ec2-system-status-failed"
  alarm_description   = "AWS 기반 호스트·네트워크 문제로 EC2 System Status Check가 실패했다."
  namespace           = "AWS/EC2"
  metric_name         = "StatusCheckFailed_System"
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 2
  datapoints_to_alarm = 2
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  treat_missing_data  = "missing"

  dimensions = {
    InstanceId = var.ec2_instance_id
  }

  alarm_actions = local.alarm_actions
  ok_actions    = local.alarm_actions
}

resource "aws_cloudwatch_metric_alarm" "instance_status_failed" {
  alarm_name          = "${local.name_prefix}-ec2-instance-status-failed"
  alarm_description   = "Guest OS·네트워크 설정 문제로 EC2 Instance Status Check가 실패했다."
  namespace           = "AWS/EC2"
  metric_name         = "StatusCheckFailed_Instance"
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 3
  datapoints_to_alarm = 3
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 1
  treat_missing_data  = "missing"

  dimensions = {
    InstanceId = var.ec2_instance_id
  }

  alarm_actions = local.alarm_actions
  ok_actions    = local.alarm_actions
}

resource "aws_cloudwatch_metric_alarm" "cpu_high" {
  alarm_name          = "${local.name_prefix}-ec2-cpu-high"
  alarm_description   = "EC2 평균 CPU가 초기 경고 임계값을 10분 동안 초과했다."
  namespace           = "AWS/EC2"
  metric_name         = "CPUUtilization"
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 2
  datapoints_to_alarm = 2
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = var.cpu_threshold_percent
  treat_missing_data  = "missing"

  dimensions = {
    InstanceId = var.ec2_instance_id
  }

  alarm_actions = local.alarm_actions
  ok_actions    = local.alarm_actions
}
