variable "environment" {
  type = string
  validation {
    condition     = contains(["staging", "production"], var.environment)
    error_message = "Staging 또는 Production만 허용합니다."
  }
}

variable "aws_account_id" {
  type = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.aws_account_id))
    error_message = "확인한 12자리 계정 ID가 필요합니다."
  }
}

variable "aws_region" {
  type    = string
  default = "ap-northeast-2"
  validation {
    condition     = var.aws_region == "ap-northeast-2"
    error_message = "서울 리전만 허용합니다."
  }
}

variable "rds_ca_bundle" {
  type      = string
  sensitive = true
}

variable "config" {
  type = object({
    vpc_id                             = string
    subnet_ids                         = list(string)
    cluster_arn                        = string
    capacity_provider                  = string
    namespace_arn                      = string
    alb_arn                            = string
    alb_listener_arn                   = string
    alb_security_group_id              = string
    database_security_group_id         = string
    secrets_endpoint_security_group_id = string
    application_domain_name            = string
    listener_rule_priority             = optional(number, 10)
    image_repository                   = string
    images = object({
      main_backend   = string
      payment_server = string
    })
    source_sha                    = string
    database_host                 = string
    database_name                 = string
    product_image_bucket          = string
    product_image_prefix          = string
    main_environment              = map(string)
    main_secret_keys              = set(string)
    payment_environment           = map(string)
    payment_secret_keys           = set(string)
    desired_count                 = optional(number, 0)
    scheduling_enabled            = optional(bool, false)
    ai_profile_scheduling_enabled = optional(bool, false)
    flyway_enabled                = optional(bool, false)
    # awsvpc(기본): Task마다 ENI·보안 그룹. bridge: 호스트 네트워크를 공유해 ENI 한도가 없고 동적 호스트 포트를 쓴다.
    network_mode = optional(string, "awsvpc")
    # bridge에서 필수. Task 대신 이 호스트(ECS 컨테이너 인스턴스) 보안 그룹에 접근 규칙을 만든다.
    container_instance_security_group_id = optional(string, null)
    traffic_cutover                      = optional(bool, false)
    traffic_health_verified              = optional(bool, false)
    activation_checks = optional(object({
      database_migrated = bool
      network_verified  = bool
      capacity_verified = bool
      secrets_ready     = bool
      images_verified   = bool
      api_compatible    = bool
      }), {
      database_migrated = false
      network_verified  = false
      capacity_verified = false
      secrets_ready     = false
      images_verified   = false
      api_compatible    = false
    })
  })

  validation {
    condition = (
      length(distinct(var.config.subnet_ids)) == 2 &&
      (var.config.desired_count == 0 || (var.config.desired_count >= 2 && floor(var.config.desired_count) == var.config.desired_count)) &&
      (!var.config.traffic_cutover || (var.config.desired_count >= 2 && var.config.traffic_health_verified)) &&
      var.config.listener_rule_priority >= 1 && var.config.listener_rule_priority <= 50000 &&
      can(regex("^[0-9a-f]{40}$", var.config.source_sha)) &&
      can(regex("^[a-z0-9][a-z0-9.-]{2,62}$", var.config.product_image_bucket)) &&
      can(regex("^[A-Za-z0-9][A-Za-z0-9._/-]*/$", var.config.product_image_prefix)) &&
      !strcontains(var.config.product_image_prefix, "..")
    )
    error_message = "서로 다른 두 App Subnet, SHA40, 확인한 상품 Prefix, 휴면0 또는 HA Task2개 이상 필요."
  }
}
