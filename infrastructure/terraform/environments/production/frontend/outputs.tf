output "production_frontend" {
  value = {
    distribution_id          = aws_cloudfront_distribution.production.id
    distribution_arn         = aws_cloudfront_distribution.production.arn
    domain_name              = aws_cloudfront_distribution.production.domain_name
    vpc_origin_id            = aws_cloudfront_vpc_origin.backend.id
    bucket                   = local.bucket_name
    asset_prefix             = local.asset_prefix
    candidate_writes_blocked = var.candidate_write_gate
    public_site_ready        = false
    traffic_switched         = false
    database_switched        = false
  }
}
