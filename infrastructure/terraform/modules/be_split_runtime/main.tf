locals {
  prefix = "seonjalal-v2-${var.environment}"
  names = {
    main_backend   = "${local.prefix}-main-backend"
    payment_server = "${local.prefix}-payment-server"
  }
  ports = { main_backend = 8080, payment_server = 8090 }
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
  }
}

resource "aws_cloudwatch_log_group" "service" {
  for_each          = local.names
  name              = "/ecs/${each.value}"
  retention_in_days = local.log_retention
}

# JSON 값은 별도 안전한 bootstrap으로 저장. Terraform State에는 Secret 값이 없다.
resource "aws_secretsmanager_secret" "runtime" {
  name                    = "seonjalal/${var.environment}/backend/split-runtime"
  recovery_window_in_days = 30
  lifecycle { prevent_destroy = true }
}

resource "aws_iam_role" "execution" {
  name               = "${local.prefix}-split-execution"
  assume_role_policy = local.trust
}
resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}
resource "aws_iam_role_policy" "secrets" {
  name = "InjectOnlySplitBackendSecret"
  role = aws_iam_role.execution.id
  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = ["secretsmanager:GetSecretValue"], Resource = aws_secretsmanager_secret.runtime.arn }]
  })
}

resource "aws_iam_role" "task" {
  for_each           = local.names
  name               = "${each.value}-task"
  assume_role_policy = local.trust
}
resource "aws_iam_role_policy" "product_images" {
  name = "ReadOnlyProductImages"
  role = aws_iam_role.task["main_backend"].id
  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = ["s3:GetObject"], Resource = "arn:aws:s3:::${var.config.product_image_bucket}/${var.config.product_image_prefix}*" }]
  })
}

resource "aws_security_group" "task" {
  for_each    = local.names
  name        = "${each.value}-runtime"
  description = "${each.value} private ECS task"
  vpc_id      = var.config.vpc_id
}

resource "aws_vpc_security_group_ingress_rule" "main_from_alb" {
  security_group_id            = aws_security_group.task["main_backend"].id
  referenced_security_group_id = var.config.alb_security_group_id
  ip_protocol                  = "tcp"
  from_port                    = 8080
  to_port                      = 8080
}
resource "aws_vpc_security_group_egress_rule" "alb_to_main" {
  security_group_id            = var.config.alb_security_group_id
  referenced_security_group_id = aws_security_group.task["main_backend"].id
  ip_protocol                  = "tcp"
  from_port                    = 8080
  to_port                      = 8080
}
resource "aws_vpc_security_group_ingress_rule" "payment_from_main" {
  security_group_id            = aws_security_group.task["payment_server"].id
  referenced_security_group_id = aws_security_group.task["main_backend"].id
  ip_protocol                  = "tcp"
  from_port                    = 8090
  to_port                      = 8090
}
resource "aws_vpc_security_group_egress_rule" "main_to_payment" {
  security_group_id            = aws_security_group.task["main_backend"].id
  referenced_security_group_id = aws_security_group.task["payment_server"].id
  ip_protocol                  = "tcp"
  from_port                    = 8090
  to_port                      = 8090
}
resource "aws_vpc_security_group_egress_rule" "database" {
  for_each                     = local.names
  security_group_id            = aws_security_group.task[each.key].id
  referenced_security_group_id = var.config.database_security_group_id
  ip_protocol                  = "tcp"
  from_port                    = 3306
  to_port                      = 3306
}
resource "aws_vpc_security_group_ingress_rule" "database" {
  for_each                     = local.names
  security_group_id            = var.config.database_security_group_id
  referenced_security_group_id = aws_security_group.task[each.key].id
  ip_protocol                  = "tcp"
  from_port                    = 3306
  to_port                      = 3306
}
resource "aws_vpc_security_group_egress_rule" "https" {
  for_each          = local.names
  security_group_id = aws_security_group.task[each.key].id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}
resource "aws_vpc_security_group_ingress_rule" "secrets_endpoint" {
  for_each                     = local.names
  security_group_id            = var.config.secrets_endpoint_security_group_id
  referenced_security_group_id = aws_security_group.task[each.key].id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
}
