variable "environment" {
  type = string
  validation {
    condition     = contains(["staging", "production"], var.environment)
    error_message = "AI ECS는 staging 또는 production만 허용."
  }
}

variable "aws_account_id" { type = string }
variable "aws_region" { type = string }
variable "rds_ca_bundle" {
  type        = string
  description = "공개 RDS CA PEM. 비밀번호나 인증키가 아님"
  validation {
    condition     = strcontains(var.rds_ca_bundle, "-----BEGIN CERTIFICATE-----")
    error_message = "RDS 서버 인증서 검증용 공개 CA PEM 필요."
  }
}

variable "config" {
  description = "실제 배포 입력. 이미지·자산·DB 준비를 확인한 값만 사용"
  type = object({
    vpc_id                            = string
    subnet_id                         = string
    cluster_arn                       = string
    capacity_provider                 = string
    database_host                     = string
    database_client_security_group_id = string
    backend_security_group_id         = string
    backend_export_security_group_id  = string
    backend_base_url                  = string
    backend_export_url                = string
    backend_categories_url            = string
    source_sha                        = string
    images                            = map(string)
    asset_bucket                      = string
    asset_prefix                      = string
    asset_manifest_key                = string
    asset_manifest_sha256             = string
    provider_models = object({
      chat     = string
      profiler = string
    })
    desired_count = optional(number, 0)
    activation_checks = optional(object({
      database_migrated         = optional(bool, false)
      catalog_seeded            = optional(bool, false)
      network_verified          = optional(bool, false)
      capacity_verified         = optional(bool, false)
      runtime_contract_verified = optional(bool, false)
      credentials_ready         = optional(bool, false)
      provider_budget_approved  = optional(bool, false)
    }), {})
  })

  validation {
    condition = (
      contains([0, 1], var.config.desired_count) &&
      can(regex("^[0-9a-f]{40}$", var.config.source_sha)) &&
      toset(keys(var.config.images)) == toset(["chat", "profiler", "search", "migrate"]) &&
      can(regex("^[0-9a-f]{64}$", var.config.asset_manifest_sha256))
    )
    error_message = "AI desired_count는 0/1, 전체 SHA와 네 Image 및 자산 Manifest SHA256 필요."
  }
  validation {
    condition = (
      can(regex("^[A-Za-z0-9][A-Za-z0-9.-]+\\.rds\\.amazonaws\\.com$", var.config.database_host)) &&
      can(regex("^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$", var.config.asset_bucket)) &&
      can(regex("^[A-Za-z0-9][A-Za-z0-9/_-]*/$", var.config.asset_prefix)) &&
      startswith(var.config.asset_manifest_key, var.config.asset_prefix) &&
      !strcontains(var.config.asset_manifest_key, "..") &&
      alltrue([for url in [var.config.backend_base_url, var.config.backend_export_url, var.config.backend_categories_url] :
        can(regex("^https?://[A-Za-z0-9.-]+(:[0-9]+)?(/[^?#]*)?$", url))
      ]) &&
      alltrue([for url in [var.config.backend_export_url, var.config.backend_categories_url] : startswith(url, "https://")]) &&
      try(regex("^https://[^/]+", var.config.backend_export_url) == regex("^https://[^/]+", var.config.backend_categories_url), false) &&
      alltrue([for model in values(var.config.provider_models) : can(regex("^[A-Za-z0-9._/-]+$", model))])
    )
    error_message = "확인한 RDS Host·정확한 S3 Prefix/Manifest·인증정보 없는 Backend URL·명시적 모델 ID 필요."
  }
}
