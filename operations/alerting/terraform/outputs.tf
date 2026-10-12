output "sns_topic_arn" {
  description = "CloudWatch Alarm이 알림을 발행하는 SNS Topic ARN"
  value       = aws_sns_topic.infrastructure_alerts.arn
}

output "lambda_function_name" {
  description = "Discord 메시지 변환 Lambda 이름"
  value       = aws_lambda_function.discord_alert.function_name
}

output "alarm_names" {
  description = "첫 단계에서 생성되는 EC2 Alarm 이름"
  value = [
    aws_cloudwatch_metric_alarm.system_status_failed.alarm_name,
    aws_cloudwatch_metric_alarm.instance_status_failed.alarm_name,
    aws_cloudwatch_metric_alarm.cpu_high.alarm_name,
  ]
}
