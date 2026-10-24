output "frontend" {
  value = {
    bucket_name               = aws_s3_bucket.frontend.bucket
    bucket_arn                = aws_s3_bucket.frontend.arn
    region                    = var.aws_region
    environment_prefixes      = local.environment_prefixes
    cloudfront_ready          = false
    staging_vpn_ready         = false
    public_site_ready         = false
    staging_cloudfront_domain = try(aws_cloudfront_distribution.staging[0].domain_name, null)
    staging_gateway_ipv4      = var.staging_gateway_ipv4
    # 공개 Distribution ID는 FE CD의 FE_CLOUDFRONT_DISTRIBUTION_ID(staging) 값으로 사용한다.
    staging_public_distribution_id = try(aws_cloudfront_distribution.staging_public[0].id, null)
    staging_public_domain          = try(aws_cloudfront_distribution.staging_public[0].domain_name, null)
  }
}
