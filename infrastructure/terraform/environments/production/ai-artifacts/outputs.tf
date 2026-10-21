output "ai_repository_urls" {
  description = "운영 AI 네 이미지 저장소 URL. 이미지 발행·Task 기동 완료를 의미하지 않음"
  value       = { for workload, repository in aws_ecr_repository.ai : workload => repository.repository_url }
}

output "ai_image_publisher_role_arn" {
  description = "AI main 전용 운영 이미지 발행 역할. ECS 배포·Secret·S3 권한 없음"
  value       = aws_iam_role.ai_image_publisher.arn
}
