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
    # 호스트 보안 그룹 규칙(ALB→호스트, 호스트 간, DB 접근)을 이 슬롯이 소유할지 여부. 두 슬롯이 모두 bridge면 한쪽만 true.
    manage_host_rules       = optional(bool, true)
    traffic_cutover         = optional(bool, false)
    traffic_health_verified = optional(bool, false)
    # ---- 블루-그린 슬롯(기본값이면 기존 단일 슬롯과 동일) ----
    # 두 번째 슬롯의 이름 접미사(예 "-b"). 서비스·Task 패밀리·Service Connect 이름에 붙는다.
    name_suffix = optional(string, "")
    # 접미사 슬롯이 기존 슬롯의 IAM Role·Secret을 재사용하도록 ARN을 전달한다. 접미사와 함께만 사용한다.
    shared_resources = optional(object({
      execution_role_arn = string
      task_role_arns = object({
        main_backend   = string
        payment_server = string
      })
      secret_arn = string
    }), null)
    # 예약 Rule(.invalid Host)의 우선순위. 두 슬롯이 겹치지 않도록 접미사 슬롯은 다른 값을 쓴다.
    parking_rule_priority = optional(number, 8)
    # 기존 슬롯의 전환 Rule에 새 슬롯 Target Group을 가중치로 섞는다(0이면 새 슬롯으로 트래픽 없음, 100이면 전부).
    green_target_group = optional(object({
      arn    = string
      weight = number
    }), null)
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
      # 전환 Rule은 HA Task 2개 이상이 있어야 한다. 단, 새 슬롯이 가중치 100을 받아 이 슬롯이 퇴역(0개)하는 경우만 예외.
      (!var.config.traffic_cutover || (var.config.traffic_health_verified && (
        var.config.desired_count >= 2 ||
        (var.config.green_target_group != null && try(var.config.green_target_group.weight, 0) == 100)
      ))) &&
      var.config.listener_rule_priority >= 1 && var.config.listener_rule_priority <= 50000 &&
      var.config.parking_rule_priority >= 1 && var.config.parking_rule_priority <= 50000 &&
      can(regex("^[0-9a-f]{40}$", var.config.source_sha)) &&
      can(regex("^[a-z0-9][a-z0-9.-]{2,62}$", var.config.product_image_bucket)) &&
      can(regex("^[A-Za-z0-9][A-Za-z0-9._/-]*/$", var.config.product_image_prefix)) &&
      !strcontains(var.config.product_image_prefix, "..")
    )
    error_message = "서로 다른 두 App Subnet, SHA40, 확인한 상품 Prefix, 휴면0 또는 HA Task2개 이상 필요."
  }
}
