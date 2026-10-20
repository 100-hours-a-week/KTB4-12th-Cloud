# 기본 RDS/ALB/EC2 지표를 읽는 exporter용. CloudWatch 유료 모니터링 옵션 활성화 없음.
resource "aws_iam_role_policy" "cloudwatch_metrics" {
  name = "ReadSeoulCloudWatchMetricsOnly"
  role = aws_iam_role.monitor.name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid    = "ReadMetricsInSeoul"
      Effect = "Allow"
      Action = [
        "cloudwatch:GetMetricData",
        "cloudwatch:GetMetricStatistics",
        "cloudwatch:ListMetrics"
      ]
      # 이 지표 조회 API는 개별 metric ARN으로 권한 범위를 지정할 수 없음.
      # 별도 쓰기/로그/Secret/DB API 권한을 부여하지 않고 요청 리전만 한정.
      Resource = "*"
      Condition = {
        StringEquals = { "aws:RequestedRegion" = "ap-northeast-2" }
      }
    }]
  })
}
