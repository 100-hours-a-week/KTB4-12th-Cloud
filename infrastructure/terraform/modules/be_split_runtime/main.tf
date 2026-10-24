locals {
  bridge = var.config.network_mode == "bridge"
  # 호스트 보안 그룹이 지정된 bridge에서만 호스트 기준 규칙을 만든다. 미지정이면 가드가 실패시킨다.
  bridge_rules  = local.bridge && var.config.container_instance_security_group_id != null && var.config.manage_host_rules
  task_sg_names = local.bridge ? {} : local.names
  # ECS Linux 기본 동적 호스트 포트 범위(bridge에서 ALB·Service Connect 트래픽에 필요).
  dynamic_port_from = 32768
  dynamic_port_to   = 65535
}

locals {
  prefix = "seonjalal-v2-${var.environment}"
  suffix = var.config.name_suffix
  shared = var.config.shared_resources
  # 접미사 슬롯은 기존 슬롯의 IAM Role·Secret·로그 그룹을 재사용하고 새로 만들지 않는다.
  owns_shared = local.shared == null
  # 로그 그룹·IAM Role 이름의 기준. 슬롯과 무관하게 접미사가 없다.
  shared_names = {
    main_backend   = "${local.prefix}-main-backend"
    payment_server = "${local.prefix}-payment-server"
  }
  # 서비스·Task 패밀리 이름. 접미사 슬롯은 기존 슬롯과 이름이 겹치지 않는다.
  names              = { for key, name in local.shared_names : key => "${name}${local.suffix}" }
  secret_arn         = local.owns_shared ? aws_secretsmanager_secret.runtime[0].arn : local.shared.secret_arn
  execution_role_arn = local.owns_shared ? aws_iam_role.execution[0].arn : local.shared.execution_role_arn
  task_role_arns     = local.owns_shared ? { for key, role in aws_iam_role.task : key => role.arn } : local.shared.task_role_arns
  log_group_names    = { for key, name in local.shared_names : key => "/ecs/${name}" }
  ports              = { main_backend = 8080, payment_server = 8090 }
  trust = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow", Principal = { Service = "ecs-tasks.amazonaws.com" }, Action = "sts:AssumeRole"
      Condition = {
        StringEquals = { "aws:SourceAccount" = var.aws_account_id }
        ArnLike      = { "aws:SourceArn" = "arn:aws:ecs:${var.aws_region}:${var.aws_account_id}:*" }
      }
    }]
  })
  required_main_secrets = toset([
    "DB_PASSWORD", "JWT_SECRET_BASE64", "LOGIN_RATE_LIMIT_HMAC_SECRET_BASE64",
    "CHAT_CONVERSATION_TOKEN_SECRET_BASE64", "AI_CHAT_SERVICE_TOKEN",
    "PROFILING_SERVICE_TOKEN", "PAYMENT_SERVER_API_KEY",
  ])
  required_main_environment = toset([
    "DB_URL", "DB_USERNAME", "JWT_ISSUER", "CORS_ALLOWED_ORIGINS",
    "CHAT_CONVERSATION_TOKEN_ISSUER", "AI_CHAT_CONNECT_TIMEOUT",
    "AI_CHAT_READ_TIMEOUT", "AI_CHAT_SSE_IDLE_TIMEOUT",
    "AI_CHAT_SSE_TOTAL_TIMEOUT", "AI_CHAT_SSE_MAX_CONNECTIONS",
  ])
  required_payment_secrets     = toset(["DB_PASSWORD", "PAYMENT_SERVER_API_KEY"])
  required_payment_environment = toset(["DB_URL", "DB_USERNAME"])
  protected_environment = toset([
    "AWS_REGION", "PRODUCT_IMAGE_BUCKET", "PRODUCT_SEARCH_ENABLED",
    "SPRING_FLYWAY_ENABLED", "SPRING_SQL_INIT_MODE", "SPRING_JPA_HIBERNATE_DDL_AUTO",
    "SCHEDULING_ENABLED", "AI_PROFILE_SCHEDULING_ENABLED", "AI_CHAT_BASE_URL",
    "AI_PROFILE_BASE_URL", "PAYMENT_SERVER_BASE_URL", "RDS_CA_BUNDLE",
    "JAVA_TOOL_OPTIONS", "JDK_JAVA_OPTIONS", "_JAVA_OPTIONS",
  ])
  jdbc_urls = [
    lookup(var.config.main_environment, "DB_URL", ""),
    lookup(var.config.payment_environment, "DB_URL", ""),
  ]
  log_retention = var.environment == "production" ? 30 : 7
}

data "aws_subnet" "app" {
  for_each = toset(var.config.subnet_ids)
  id       = each.key
}
data "aws_security_group" "alb" { id = var.config.alb_security_group_id }
data "aws_security_group" "database" { id = var.config.database_security_group_id }
data "aws_security_group" "secrets_endpoint" { id = var.config.secrets_endpoint_security_group_id }
data "aws_security_group" "instance" {
  count = local.bridge && var.config.container_instance_security_group_id != null ? 1 : 0
  id    = var.config.container_instance_security_group_id
}
data "aws_lb" "existing" { arn = var.config.alb_arn }
data "aws_lb_listener" "https" { arn = var.config.alb_listener_arn }

resource "terraform_data" "guard" {
  lifecycle {
    precondition {
      condition = (
        var.config.cluster_arn == "arn:aws:ecs:${var.aws_region}:${var.aws_account_id}:cluster/${local.prefix}-cluster" &&
        startswith(var.config.capacity_provider, "${local.prefix}-") &&
        can(regex("^arn:aws:servicediscovery:${var.aws_region}:${var.aws_account_id}:namespace/ns-[a-z0-9]+$", var.config.namespace_arn)) &&
        alltrue([for subnet in data.aws_subnet.app : subnet.vpc_id == var.config.vpc_id && !subnet.map_public_ip_on_launch]) &&
        length(distinct([for subnet in data.aws_subnet.app : subnet.availability_zone])) == 2 &&
        data.aws_security_group.alb.vpc_id == var.config.vpc_id &&
        data.aws_security_group.database.vpc_id == var.config.vpc_id &&
        data.aws_security_group.secrets_endpoint.vpc_id == var.config.vpc_id &&
        data.aws_lb.existing.vpc_id == var.config.vpc_id &&
        data.aws_lb.existing.internal &&
        data.aws_lb_listener.https.load_balancer_arn == var.config.alb_arn &&
        data.aws_lb_listener.https.port == (var.environment == "production" ? 8443 : 443) &&
        data.aws_lb_listener.https.protocol == "HTTPS" &&
        (var.environment != "production" || var.config.application_domain_name == "v2-origin.seonjalal.com")
      )
      error_message = "같은 VPC의 두 AZ Private Subnet·환경별 내부 HTTPS Listener(운영8443/스테이징443)·해당 환경 Cluster/Namespace 필요."
    }
    precondition {
      # bridge는 Task 보안 그룹이 없으므로 호스트 보안 그룹이 같은 VPC에 있어야 한다.
      # 호스트 보안 그룹이 Secrets 엔드포인트 443에 접근 가능한 규칙은 호스트를 소유한 Root가 이미 가지고 있어야 한다.
      condition = (
        contains(["awsvpc", "bridge"], var.config.network_mode) &&
        (!local.bridge || (
          var.config.container_instance_security_group_id != null &&
          length(data.aws_security_group.instance) == 1 &&
          try(data.aws_security_group.instance[0].vpc_id, "") == var.config.vpc_id
        ))
      )
      error_message = "network_mode는 awsvpc 또는 bridge이며, bridge는 같은 VPC의 container_instance_security_group_id가 필요합니다."
    }
    precondition {
      condition = (
        var.config.image_repository == "seonjalal/production/backend" &&
        alltrue([for image in values(var.config.images) : can(regex("^${var.aws_account_id}\\.dkr\\.ecr\\.${var.aws_region}\\.amazonaws\\.com/${var.config.image_repository}@sha256:[0-9a-f]{64}$", image))])
      )
      error_message = "동일 계정 서울 ECR의 승인한 환경별 Backend 저장소와 Digest 고정 이미지 필요."
    }
    precondition {
      condition = (
        length(setsubtract(local.required_main_environment, toset(keys(var.config.main_environment)))) == 0 &&
        length(setsubtract(local.required_main_secrets, var.config.main_secret_keys)) == 0 &&
        length(setsubtract(local.required_payment_environment, toset(keys(var.config.payment_environment)))) == 0 &&
        length(setsubtract(local.required_payment_secrets, var.config.payment_secret_keys)) == 0 &&
        length(setintersection(local.protected_environment, toset(concat(keys(var.config.main_environment), keys(var.config.payment_environment))))) == 0 &&
        length(setintersection(toset(keys(var.config.main_environment)), var.config.main_secret_keys)) == 0 &&
        length(setintersection(toset(keys(var.config.payment_environment)), var.config.payment_secret_keys)) == 0 &&
        alltrue([for key in concat(keys(var.config.main_environment), keys(var.config.payment_environment)) :
          key == "CHAT_CONVERSATION_TOKEN_ISSUER" ||
          !can(regex("(?i)(password|secret|token|api_key|access_key|credential|webhook|dsn)", key))
        ])
      )
      error_message = "BE 두 컨테이너의 필수 설정·Secret JSON 키가 부족하거나 평문/보호 설정이 중복되었습니다."
    }
    precondition {
      condition = alltrue([for url in local.jdbc_urls :
        startswith(url, "jdbc:mysql://${var.config.database_host}:3306/${var.config.database_name}?") &&
        length(regexall("(?i)[?&]sslMode=", url)) == 1 &&
        can(regex("(?i)[?&]sslMode=VERIFY_IDENTITY(?:&|$)", url)) &&
        length(regexall("(?i)[?&](user|password|trustCertificateKeyStorePassword|allowPublicKeyRetrieval|useSSL)=", url)) == 0
      ])
      error_message = "Main/Payment 모두 지정 MySQL RDS/DB와 JDBC VERIFY_IDENTITY 한 개가 필요합니다."
    }
    precondition {
      # Flyway는 앱 런타임 계정과 분리된 전용 계정으로 실행한다(앱 계정에 DDL 권한을 주지 않기 위함).
      # 비밀번호는 평문 환경변수가 아니라 Secret JSON 키로만 주입한다.
      condition = !var.config.flyway_enabled || (
        contains(keys(var.config.main_environment), "SPRING_FLYWAY_USER") &&
        contains(var.config.main_secret_keys, "SPRING_FLYWAY_PASSWORD") &&
        lookup(var.config.main_environment, "SPRING_FLYWAY_USER", "") != lookup(var.config.main_environment, "DB_USERNAME", "")
      )
      error_message = "Flyway 자동 실행에는 앱 계정과 다른 SPRING_FLYWAY_USER(환경변수)와 SPRING_FLYWAY_PASSWORD(Secret 키)가 필요합니다."
    }
    precondition {
      condition     = var.config.desired_count == 0 || alltrue(values(var.config.activation_checks))
      error_message = "기동 전 Migration·네트워크·용량·Secret·이미지·API 호환성 확인이 필요합니다."
    }
    precondition {
      # 접미사 슬롯은 공유 자원 재사용이 필수이고, 자체 전환 Rule과 가중치 Rule을 만들 수 없다(전환 Rule은 기존 슬롯 하나만 소유).
      condition = (
        can(regex("^(-[a-z0-9]{1,8})?$", local.suffix)) &&
        (local.suffix != "") == (local.shared != null) &&
        (local.suffix == "" || (!var.config.traffic_cutover && var.config.green_target_group == null))
      )
      error_message = "name_suffix는 '-영숫자' 형식이며 shared_resources와 함께만 쓰고, 접미사 슬롯은 traffic_cutover·green_target_group을 사용할 수 없습니다."
    }
    precondition {
      condition = var.config.green_target_group == null || (
        var.config.traffic_cutover &&
        var.config.green_target_group.weight >= 0 &&
        var.config.green_target_group.weight <= 100 &&
        floor(var.config.green_target_group.weight) == var.config.green_target_group.weight
      )
      error_message = "green_target_group은 traffic_cutover가 켜진 기존 슬롯에서만, 0~100 정수 가중치로 사용할 수 있습니다."
    }
  }
}

# 단일 리소스에 count를 도입하면서 기존 State 주소를 새 주소로 옮긴다. 실제 AWS 리소스는 변경되지 않는다.
moved {
  from = aws_secretsmanager_secret.runtime
  to   = aws_secretsmanager_secret.runtime[0]
}
moved {
  from = aws_iam_role.execution
  to   = aws_iam_role.execution[0]
}
moved {
  from = aws_iam_role_policy_attachment.execution
  to   = aws_iam_role_policy_attachment.execution[0]
}
moved {
  from = aws_iam_role_policy.secrets
  to   = aws_iam_role_policy.secrets[0]
}
moved {
  from = aws_iam_role_policy.product_images
  to   = aws_iam_role_policy.product_images[0]
}

# 아래 공유 자원은 기존 슬롯만 소유한다. 접미사 슬롯은 shared_resources로 ARN을 받아 재사용한다.
resource "aws_cloudwatch_log_group" "service" {
  for_each          = local.owns_shared ? local.shared_names : {}
  name              = "/ecs/${each.value}"
  retention_in_days = local.log_retention
}

# JSON 값은 별도 안전한 bootstrap으로 저장. Terraform State에는 Secret 값이 없다.
resource "aws_secretsmanager_secret" "runtime" {
  count                   = local.owns_shared ? 1 : 0
  name                    = "seonjalal/${var.environment}/backend/split-runtime"
  recovery_window_in_days = 30
  lifecycle { prevent_destroy = true }
}

resource "aws_iam_role" "execution" {
  count              = local.owns_shared ? 1 : 0
  name               = "${local.prefix}-split-execution"
  assume_role_policy = local.trust
}
resource "aws_iam_role_policy_attachment" "execution" {
  count      = local.owns_shared ? 1 : 0
  role       = aws_iam_role.execution[0].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}
resource "aws_iam_role_policy" "secrets" {
  count = local.owns_shared ? 1 : 0
  name  = "InjectOnlySplitBackendSecret"
  role  = aws_iam_role.execution[0].id
  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = ["secretsmanager:GetSecretValue"], Resource = aws_secretsmanager_secret.runtime[0].arn }]
  })
}

resource "aws_iam_role" "task" {
  for_each           = local.owns_shared ? local.shared_names : {}
  name               = "${each.value}-task"
  assume_role_policy = local.trust
}
resource "aws_iam_role_policy" "product_images" {
  count = local.owns_shared ? 1 : 0
  name  = "ReadOnlyProductImages"
  role  = aws_iam_role.task["main_backend"].id
  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = ["s3:GetObject"], Resource = "arn:aws:s3:::${var.config.product_image_bucket}/${var.config.product_image_prefix}*" }]
  })
}

resource "aws_security_group" "task" {
  for_each    = local.task_sg_names
  name        = "${each.value}-runtime"
  description = "${each.value} private ECS task"
  vpc_id      = var.config.vpc_id
}

resource "aws_vpc_security_group_ingress_rule" "main_from_alb" {
  count                        = local.bridge ? 0 : 1
  security_group_id            = aws_security_group.task["main_backend"].id
  referenced_security_group_id = var.config.alb_security_group_id
  ip_protocol                  = "tcp"
  from_port                    = 8080
  to_port                      = 8080
}
resource "aws_vpc_security_group_egress_rule" "alb_to_main" {
  count                        = local.bridge ? 0 : 1
  security_group_id            = var.config.alb_security_group_id
  referenced_security_group_id = aws_security_group.task["main_backend"].id
  ip_protocol                  = "tcp"
  from_port                    = 8080
  to_port                      = 8080
}
resource "aws_vpc_security_group_ingress_rule" "payment_from_main" {
  count                        = local.bridge ? 0 : 1
  security_group_id            = aws_security_group.task["payment_server"].id
  referenced_security_group_id = aws_security_group.task["main_backend"].id
  ip_protocol                  = "tcp"
  from_port                    = 8090
  to_port                      = 8090
}
resource "aws_vpc_security_group_egress_rule" "main_to_payment" {
  count                        = local.bridge ? 0 : 1
  security_group_id            = aws_security_group.task["main_backend"].id
  referenced_security_group_id = aws_security_group.task["payment_server"].id
  ip_protocol                  = "tcp"
  from_port                    = 8090
  to_port                      = 8090
}
resource "aws_vpc_security_group_egress_rule" "database" {
  for_each                     = local.task_sg_names
  security_group_id            = aws_security_group.task[each.key].id
  referenced_security_group_id = var.config.database_security_group_id
  ip_protocol                  = "tcp"
  from_port                    = 3306
  to_port                      = 3306
}
resource "aws_vpc_security_group_ingress_rule" "database" {
  for_each                     = local.task_sg_names
  security_group_id            = var.config.database_security_group_id
  referenced_security_group_id = aws_security_group.task[each.key].id
  ip_protocol                  = "tcp"
  from_port                    = 3306
  to_port                      = 3306
}
resource "aws_vpc_security_group_egress_rule" "https" {
  for_each          = local.task_sg_names
  security_group_id = aws_security_group.task[each.key].id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}
resource "aws_vpc_security_group_ingress_rule" "secrets_endpoint" {
  for_each                     = local.task_sg_names
  security_group_id            = var.config.secrets_endpoint_security_group_id
  referenced_security_group_id = aws_security_group.task[each.key].id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
}

# ---- network_mode = "bridge" 전용: Task 대신 호스트 보안 그룹 기준 규칙 ----
# ALB → 호스트의 동적 포트(Main의 호스트 포트).
resource "aws_vpc_security_group_ingress_rule" "instance_from_alb" {
  count                        = local.bridge_rules ? 1 : 0
  security_group_id            = var.config.container_instance_security_group_id
  referenced_security_group_id = var.config.alb_security_group_id
  ip_protocol                  = "tcp"
  from_port                    = local.dynamic_port_from
  to_port                      = local.dynamic_port_to
  description                  = "ALB to ECS bridge tasks (dynamic host ports)"
}
resource "aws_vpc_security_group_egress_rule" "alb_to_instances" {
  count                        = local.bridge_rules ? 1 : 0
  security_group_id            = var.config.alb_security_group_id
  referenced_security_group_id = var.config.container_instance_security_group_id
  ip_protocol                  = "tcp"
  from_port                    = local.dynamic_port_from
  to_port                      = local.dynamic_port_to
  description                  = "ALB to ECS bridge tasks (dynamic host ports)"
}
# Service Connect: 호스트 간 Main → Payment 프록시 통신에 호스트끼리 동적 포트가 열려야 한다.
resource "aws_vpc_security_group_ingress_rule" "instance_self" {
  count                        = local.bridge_rules ? 1 : 0
  security_group_id            = var.config.container_instance_security_group_id
  referenced_security_group_id = var.config.container_instance_security_group_id
  ip_protocol                  = "tcp"
  from_port                    = local.dynamic_port_from
  to_port                      = local.dynamic_port_to
  description                  = "Service Connect between ECS bridge hosts"
}
# 호스트 보안 그룹의 송신 규칙. 운영 호스트 SG는 443 송신만 허용하므로 bridge Task가 쓰는 두 경로를 명시한다.
# 호스트 → DB. Task 보안 그룹이 없으므로 Task의 DB 연결은 호스트 SG에서 나간다.
resource "aws_vpc_security_group_egress_rule" "instance_to_database" {
  count                        = local.bridge_rules ? 1 : 0
  security_group_id            = var.config.container_instance_security_group_id
  referenced_security_group_id = var.config.database_security_group_id
  ip_protocol                  = "tcp"
  from_port                    = 3306
  to_port                      = 3306
  description                  = "ECS bridge hosts to MySQL"
}
# 호스트 → 호스트. Service Connect로 Main이 다른 호스트의 Payment 프록시 포트로 나간다.
resource "aws_vpc_security_group_egress_rule" "instance_self" {
  count                        = local.bridge_rules ? 1 : 0
  security_group_id            = var.config.container_instance_security_group_id
  referenced_security_group_id = var.config.container_instance_security_group_id
  ip_protocol                  = "tcp"
  from_port                    = local.dynamic_port_from
  to_port                      = local.dynamic_port_to
  description                  = "Service Connect between ECS bridge hosts (egress)"
}
# DB는 Task 보안 그룹 대신 호스트 보안 그룹을 허용한다(호스트의 모든 Task가 같은 접근 범위).
resource "aws_vpc_security_group_ingress_rule" "database_from_instances" {
  count                        = local.bridge_rules ? 1 : 0
  security_group_id            = var.config.database_security_group_id
  referenced_security_group_id = var.config.container_instance_security_group_id
  ip_protocol                  = "tcp"
  from_port                    = 3306
  to_port                      = 3306
  description                  = "ECS bridge hosts to MySQL"
}
