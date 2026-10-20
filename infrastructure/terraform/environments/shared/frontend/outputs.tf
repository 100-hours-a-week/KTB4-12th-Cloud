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
  }
}
